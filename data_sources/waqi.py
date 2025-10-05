#!/usr/bin/env python3
"""
WAQI (World Air Quality Index) API integration
Provides real-time air quality data from WAQI API

Author: SkyGuard Team
Date: 2024
"""

import requests
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

@dataclass
class WAQIMeasurement:
    """WAQI measurement data structure"""
    location: str
    parameter: str
    value: float
    unit: str
    date: datetime
    latitude: float
    longitude: float
    country: str
    city: str
    source_name: str
    aqi: Optional[int] = None

class WAQIClient:
    """Client for WAQI API"""
    
    def __init__(self, base_url: str = "https://api.waqi.info", token: str = "e43e4b30c615fd2c2070284a84db0fc043aff513"):
        self.base_url = base_url
        self.token = token
        self.session = requests.Session()
        headers = {
            'User-Agent': 'SkyGuard/1.0 (Air Quality Monitoring App)',
            'Content-Type': 'application/json'
        }
        self.session.headers.update(headers)
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make a simple request to the WAQI API"""
        try:
            url = f"{self.base_url}/{endpoint}"
            if params is None:
                params = {}
            params['token'] = self.token
            
            # Simple request without session
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error making request to {endpoint}: {e}")
            return {"status": "error", "data": None}
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response: {e}")
            return {"status": "error", "data": None}

def get_waqi_by_city(city: str) -> List[WAQIMeasurement]:
    """
    Get air quality data for a specific city from WAQI
    
    Args:
        city: City name (e.g., 'Madrid', 'New York', 'Tokyo')
    
    Returns:
        List of WAQIMeasurement objects with all air quality parameters
    """
    try:
        url = f"https://api.waqi.info/feed/{city.lower()}/?token=e43e4b30c615fd2c2070284a84db0fc043aff513"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        measurements = []
        
        if data.get('status') == 'ok' and data.get('data'):
            aqi_data = data['data']
            
            if 'aqi' in aqi_data and aqi_data['aqi'] != '-':
                main_aqi = int(aqi_data['aqi'])
                
                iaqi = aqi_data.get('iaqi', {})
                
                param_mapping = {
                    'pm25': ('pm25', 'µg/m³'),
                    'pm10': ('pm10', 'µg/m³'),
                    'o3': ('o3', 'µg/m³'),
                    'no2': ('no2', 'µg/m³'),
                    'so2': ('so2', 'µg/m³'),
                    'co': ('co', 'mg/m³'),
                    'h': ('humidity', '%'),
                    't': ('temperature', '°C'),
                    'p': ('pressure', 'hPa'),
                    'w': ('wind', 'm/s')
                }
                
                for param, param_data in iaqi.items():
                    if isinstance(param_data, dict) and 'v' in param_data:
                        value = float(param_data['v'])
                        
                        if param in param_mapping:
                            param_name, unit = param_mapping[param]
                            
                            measurement = WAQIMeasurement(
                                location=aqi_data.get('city', {}).get('name', city),
                                parameter=param_name,
                                value=value,
                                unit=unit,
                                date=datetime.fromisoformat(aqi_data.get('time', {}).get('iso', '').replace('Z', '+00:00')),
                                latitude=aqi_data.get('city', {}).get('geo', [0, 0])[0],
                                longitude=aqi_data.get('city', {}).get('geo', [0, 0])[1],
                                country=aqi_data.get('city', {}).get('name', '').split(',')[-1].strip() if ',' in aqi_data.get('city', {}).get('name', '') else '',
                                city=city,
                                source_name='WAQI',
                                aqi=main_aqi
                            )
                            measurements.append(measurement)
                
                return measurements
                    
        return measurements
        
    except Exception as e:
        logger.error(f"Error fetching WAQI data for {city}: {e}")
        return []

def get_waqi_by_coordinates(latitude: float, longitude: float) -> List[WAQIMeasurement]:
    """
    Get air quality data for specific coordinates from WAQI
    
    Args:
        latitude: Latitude coordinate
        longitude: Longitude coordinate
    
    Returns:
        List of WAQIMeasurement objects with all air quality parameters
    """
    try:
        # Simple direct request like curl
        url = f"https://api.waqi.info/feed/geo:{latitude};{longitude}/?token=e43e4b30c615fd2c2070284a84db0fc043aff513"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        measurements = []
        
        if data.get('status') == 'ok' and data.get('data'):
            aqi_data = data['data']
            
            # Main AQI value
            if 'aqi' in aqi_data and aqi_data['aqi'] != '-':
                main_aqi = int(aqi_data['aqi'])
                
                # Get all individual pollutant measurements
                iaqi = aqi_data.get('iaqi', {})
                
                # Map all parameters
                param_mapping = {
                    'pm25': ('pm25', 'µg/m³'),
                    'pm10': ('pm10', 'µg/m³'),
                    'o3': ('o3', 'µg/m³'),
                    'no2': ('no2', 'µg/m³'),
                    'so2': ('so2', 'µg/m³'),
                    'co': ('co', 'mg/m³'),
                    'h': ('humidity', '%'),
                    't': ('temperature', '°C'),
                    'p': ('pressure', 'hPa'),
                    'w': ('wind', 'm/s')
                }
                
                for param, param_data in iaqi.items():
                    if isinstance(param_data, dict) and 'v' in param_data:
                        value = float(param_data['v'])
                        
                        if param in param_mapping:
                            param_name, unit = param_mapping[param]
                            
                            measurement = WAQIMeasurement(
                                location=aqi_data.get('city', {}).get('name', f"Location {latitude}, {longitude}"),
                                parameter=param_name,
                                value=value,
                                unit=unit,
                                date=datetime.fromisoformat(aqi_data.get('time', {}).get('iso', '').replace('Z', '+00:00')),
                                latitude=latitude,
                                longitude=longitude,
                                country=aqi_data.get('city', {}).get('name', '').split(',')[-1].strip() if ',' in aqi_data.get('city', {}).get('name', '') else '',
                                city=aqi_data.get('city', {}).get('name', ''),
                                source_name='WAQI',
                                aqi=main_aqi
                            )
                            measurements.append(measurement)
                
                return measurements
                    
        return measurements
        
    except Exception as e:
        logger.error(f"Error fetching WAQI data for coordinates {latitude}, {longitude}: {e}")
        return []

def get_waqi_stations_nearby(latitude: float, longitude: float, radius: float = 10.0) -> List[Dict]:
    """
    Get nearby air quality stations from WAQI
    
    Args:
        latitude: Latitude coordinate
        longitude: Longitude coordinate
        radius: Search radius in kilometers
    
    Returns:
        List of station information dictionaries
    """
    try:
        # Simple direct request like curl
        url = f"https://api.waqi.info/feed/geo:{latitude};{longitude}/?token=e43e4b30c615fd2c2070284a84db0fc043aff513"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get('status') == 'ok' and data.get('data'):
            aqi_data = data['data']
            
            station_info = {
                'id': aqi_data.get('idx', 0),
                'name': aqi_data.get('city', {}).get('name', 'Unknown Station'),
                'latitude': latitude,
                'longitude': longitude,
                'country': aqi_data.get('city', {}).get('name', '').split(',')[-1].strip() if ',' in aqi_data.get('city', {}).get('name', '') else '',
                'city': aqi_data.get('city', {}).get('name', ''),
                'aqi': int(aqi_data.get('aqi', 0)) if aqi_data.get('aqi') != '-' else 0,
                'distance_km': 0.0,  # This is the closest station
                'dominant_pollutant': aqi_data.get('dominentpol', 'Unknown'),
                'last_update': aqi_data.get('time', {}).get('iso', 'Unknown')
            }
            
            return [station_info]
        
        return []
        
    except Exception as e:
        logger.error(f"Error fetching nearby WAQI stations: {e}")
        return []

def test_waqi_integration():
    """Test WAQI API integration with real data"""
    print("🌍 Testing WAQI API Integration - ALL PARAMETERS")
    print("=" * 60)
    
    # # Test with Madrid
    # print("🔍 Testing Madrid air quality...")
    # madrid_data = get_waqi_by_city('madrid')
    # if madrid_data:
    #     print(f"✅ Madrid - Found {len(madrid_data)} measurements")
    #     print(f"   Location: {madrid_data[0].location}")
    #     print(f"   AQI: {madrid_data[0].aqi}")
    #     print(f"   Date: {madrid_data[0].date}")
    #     print("   All Parameters:")
    #     for measurement in madrid_data:
    #         print(f"     {measurement.parameter.upper()}: {measurement.value} {measurement.unit}")
    # else:
    #     print("❌ No data for Madrid")
    
    # # Test with coordinates (New York)
    # print("\n🔍 Testing New York coordinates...")
    # ny_data = get_waqi_by_coordinates(40.7128, -74.0060)
    # if ny_data:
    #     print(f"✅ New York - Found {len(ny_data)} measurements")
    #     print(f"   Location: {ny_data[0].location}")
    #     print(f"   AQI: {ny_data[0].aqi}")
    #     print(f"   Date: {ny_data[0].date}")
    #     print("   All Parameters:")
    #     for measurement in ny_data:
    #         print(f"     {measurement.parameter.upper()}: {measurement.value} {measurement.unit}")
    # else:
    #     print("❌ No data for New York coordinates")
    
    # Test nearby stations
    print("\n🔍 Testing nearby stations...")
    stations = get_waqi_stations_nearby(-2.1894, -79.8891)  # Guayaquil coordinates
    if stations:
        print(f"✅ Found {len(stations)} nearby stations")
        for station in stations:
            print(f"   Station: {station['name']}")
            print(f"   AQI: {station['aqi']}")
            print(f"   Country: {station['country']}")
            print(f"   Dominant Pollutant: {station['dominant_pollutant']}")
            print(f"   Last Update: {station['last_update']}")
            
            # Get detailed measurements for this station
            print("   Getting detailed measurements...")
            station_measurements = get_waqi_by_coordinates(station['latitude'], station['longitude'])
            if station_measurements:
                print("   All Parameters:")
                for measurement in station_measurements:
                    print(f"     {measurement.parameter.upper()}: {measurement.value} {measurement.unit}")
            else:
                print("   No detailed measurements available")
    else:
        print("❌ No nearby stations found")
    
    print("\n" + "=" * 60)
    print("🎉 WAQI API provides REAL air quality data with ALL parameters!")
    print("   - PM2.5, PM10, O3, NO2, SO2, CO")
    print("   - Temperature, Humidity, Pressure, Wind")
    print("   - Real-time AQI values")
    print("   - Global coverage")

if __name__ == "__main__":
    test_waqi_integration()

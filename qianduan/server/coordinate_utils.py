"""Approximate WGS84 -> GCJ-02 for displaying raw GPS on AMap mainland-China tiles.

Store and transmit WGS84 unchanged; convert only map visualization coordinates.
This common mathematical approximation is not a surveyed precision conversion.
"""
import math


def _out_of_china(lon, lat):
    return lon < 73.66 or lon > 135.05 or lat < 3.86 or lat > 53.55


def _transform_lat(x, y):
    return (-100.0 + 2.0*x + 3.0*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
            + (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
            + (20*math.sin(y*math.pi) + 40*math.sin(y/3*math.pi))*2/3
            + (160*math.sin(y/12*math.pi) + 320*math.sin(y*math.pi/30))*2/3)


def _transform_lon(x, y):
    return (300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
            + (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
            + (20*math.sin(x*math.pi) + 40*math.sin(x/3*math.pi))*2/3
            + (150*math.sin(x/12*math.pi) + 300*math.sin(x/30*math.pi))*2/3)


def wgs84_to_gcj02(lon, lat):
    lon, lat = float(lon), float(lat)
    if _out_of_china(lon, lat):
        return lon, lat
    a, ee = 6378245.0, 0.00669342162296594323
    dlat = _transform_lat(lon-105, lat-35)
    dlon = _transform_lon(lon-105, lat-35)
    radlat = lat / 180 * math.pi
    magic = 1 - ee * math.sin(radlat)**2
    sqrtmagic = math.sqrt(magic)
    dlat = dlat * 180 / ((a*(1-ee))/(magic*sqrtmagic)*math.pi)
    dlon = dlon * 180 / (a/sqrtmagic*math.cos(radlat)*math.pi)
    return lon + dlon, lat + dlat

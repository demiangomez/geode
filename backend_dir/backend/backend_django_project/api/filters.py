from rest_framework import generics
from django_filters import rest_framework as filters
from . import models


class StationFilter(filters.FilterSet):
    network_code = filters.CharFilter(
        field_name='network_code__network_code', lookup_expr='exact')

    station_name = filters.CharFilter(
        field_name='station_name', lookup_expr='istartswith')

    station_code = filters.CharFilter(
        field_name='station_code', lookup_expr='istartswith')

    class Meta:
        model = models.Stations
        fields = ['network_code', 'station_name',
                  'station_code', 'country_code']


class StationinfoFilter(filters.FilterSet):

    class Meta:
        model = models.Stationinfo
        fields = ['network_code', 'station_code',
                  'receiver_code', 'receiver_serial']


class GamitHtcFilter(filters.FilterSet):

    class Meta:
        model = models.GamitHtc
        fields = ['antenna_code']


class RinexFilter(filters.FilterSet):

    class Meta:
        model = models.Rinex
        fields = ['network_code', 'station_code',
                  'receiver_type', 'receiver_serial']


class RolePersonStationFilter(filters.FilterSet):
    station_api_id = filters.CharFilter(
        field_name='station')

    class Meta:
        model = models.RolePersonStation
        fields = ['station_api_id']


class StationImagesFilter(filters.FilterSet):
    station_api_id = filters.CharFilter(
        field_name='station')

    class Meta:
        model = models.StationImages
        fields = ['station_api_id']


class StationAttachedFilesFilter(filters.FilterSet):
    station_api_id = filters.CharFilter(
        field_name='station')

    class Meta:
        model = models.StationAttachedFiles
        fields = ['station_api_id']


class VisitFilter(filters.FilterSet):
    station_api_id = filters.CharFilter(
        field_name='station')

    campaign_id = filters.NumberFilter(
        field_name='campaign')

    class Meta:
        model = models.Visits
        fields = ['station_api_id', "campaign"]


class VisitAttachedFilesFilter(filters.FilterSet):
    visit_api_id = filters.CharFilter(
        field_name='visit')

    station_api_id = filters.CharFilter(field_name='visit__station')

    class Meta:
        model = models.VisitAttachedFiles
        fields = ['visit_api_id', 'station_api_id']


class VisitImagesFilter(filters.FilterSet):
    visit_api_id = filters.CharFilter(
        field_name='visit')

    station_api_id = filters.CharFilter(field_name='visit__station')

    class Meta:
        model = models.VisitImages
        fields = ['visit_api_id', 'station_api_id']


class VisitGNSSDataFilesFilter(filters.FilterSet):
    visit_api_id = filters.CharFilter(
        field_name='visit')

    station_api_id = filters.CharFilter(field_name='visit__station')

    class Meta:
        model = models.VisitGNSSDataFiles
        fields = ['visit_api_id', 'station_api_id']


class EndpointsClusterFilter(filters.FilterSet):
    role_type = filters.CharFilter(
        field_name='role_type', lookup_expr='icontains')

    class Meta:
        model = models.EndPointsCluster
        fields = ['role_type']


class EventsFilter(filters.FilterSet):
    event_date_since = filters.DateTimeFilter(
        field_name='event_date', lookup_expr='gte')
    event_date_until = filters.DateTimeFilter(
        field_name='event_date', lookup_expr='lte')
    event_type = filters.CharFilter(
        field_name='event_type', lookup_expr='icontains')
    network_code = filters.CharFilter(
        field_name='network_code', lookup_expr='exact')
    station_code = filters.CharFilter(
        field_name='station_code', lookup_expr='exact')
    year = filters.NumberFilter(field_name='year')
    doy = filters.NumberFilter(field_name='doy')
    description = filters.CharFilter(
        field_name='description', lookup_expr='icontains')
    stack = filters.CharFilter(field_name='stack', lookup_expr='icontains')
    module = filters.CharFilter(field_name='module', lookup_expr='icontains')
    node = filters.CharFilter(field_name='node', lookup_expr='icontains')

    class Meta:
        model = models.Events
        fields = ['event_date_since', 'event_date_until']


class EarthquakesFilter(filters.FilterSet):
    date_start = filters.DateTimeFilter(
        field_name='date', lookup_expr='gte')
    date_end = filters.DateTimeFilter(
        field_name='date', lookup_expr='lte')
    max_magnitude = filters.NumberFilter(
        field_name='mag', lookup_expr='lte')
    min_magnitude = filters.NumberFilter(
        field_name='mag', lookup_expr='gte')
    max_depth = filters.NumberFilter(
        field_name='depth', lookup_expr='lte')
    min_depth = filters.NumberFilter(
        field_name='depth', lookup_expr='gte')
    id = filters.CharFilter(field_name='id', lookup_expr='icontains')

    class Meta:
        model = models.Earthquakes
        fields = ['date_start', 'date_end', 'max_magnitude', 'min_magnitude',
                  'max_depth', 'min_depth', 'id']


class SourcesStationsFilter(filters.FilterSet):

    class Meta:
        model = models.SourcesStations
        fields = ['network_code', 'station_code', 'server_id']

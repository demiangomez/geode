from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.postgres.fields import ArrayField
from auditlog.registry import auditlog
from . import custom_fields
import sys
import inspect
import os.path
from django.contrib.postgres.fields import ArrayField
import datetime
# ------------------------------MODELS BASED ON EXISTING DB-----------------------------
from decimal import Decimal
from django.conf import settings
import grp
from . import utils


class BaseModel(models.Model):
    """
    This model is used to remove trailing zeros from Decimal fields that map to a numeric field with no given precision in the database.
    If the numeric field has a specified precision, there is no way to avoid saving the trailing zeros as Postgres itself adds them.
    """
    class Meta:
        abstract = True  # This model will not create a database table

    def save(self, *args, **kwargs):
        # Normalize decimal fields before saving
        for field in self._meta.get_fields():
            if isinstance(field, models.DecimalField):
                value = getattr(self, field.name)
                if value is not None:
                    normalized_value = Decimal(value).normalize()
                    setattr(self, field.name, normalized_value)

        # Call the parent class's save method
        super(BaseModel, self).save(*args, **kwargs)


class Antennas(BaseModel):
    # Field name made lowercase.
    antenna_code = models.CharField(
        db_column='AntennaCode', max_length=22)
    # Field name made lowercase.
    antenna_description = models.CharField(
        db_column='AntennaDescription', blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        ordering = ["antenna_code"]
        db_table = 'antennas'


class AprCoords(BaseModel):
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode')
    # Field name made lowercase.
    f_year = models.DecimalField(
        db_column='FYear', max_digits=120, decimal_places=50, blank=True, null=True)
    x = models.DecimalField(
        max_digits=120, decimal_places=50, blank=True, null=True)
    y = models.DecimalField(
        max_digits=120, decimal_places=50, blank=True, null=True)
    z = models.DecimalField(
        max_digits=120, decimal_places=50, blank=True, null=True)
    sn = models.DecimalField(
        max_digits=120, decimal_places=50, blank=True, null=True)
    se = models.DecimalField(
        max_digits=120, decimal_places=50, blank=True, null=True)
    su = models.DecimalField(
        max_digits=120, decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    reference_frame = models.CharField(
        db_column='ReferenceFrame', max_length=20, blank=True, null=True)
    year = models.IntegerField(db_column='Year')  # Field name made lowercase.
    doy = models.IntegerField(db_column='DOY')  # Field name made lowercase.
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'apr_coords'
        unique_together = (('network_code', 'station_code', 'year', 'doy'),)


class AwsSync(BaseModel):
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode')
    # Field name made lowercase.
    station_alias = models.CharField(db_column='StationAlias', max_length=4)
    # Field name made lowercase.
    year = models.DecimalField(
        db_column='Year', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    doy = models.DecimalField(
        db_column='DOY', max_digits=65535, decimal_places=65535)
    sync_date = models.DateTimeField(blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'aws_sync'
        unique_together = (('network_code', 'station_code', 'year', 'doy'),)


class DataSource(BaseModel):
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode', max_length=4)
    try_order = models.DecimalField(max_digits=65535, decimal_places=65535)
    protocol = models.CharField()
    fqdn = models.CharField()
    username = models.CharField(blank=True, null=True)
    password = models.CharField(blank=True, null=True)
    path = models.CharField(blank=True, null=True)
    format = models.CharField(blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'data_source'
        unique_together = (('network_code', 'station_code', 'try_order'),)


class Earthquakes(BaseModel):
    # The composite primary key (date, lat, lon) found, that is not supported. The first column is selected.
    date = models.DateTimeField()
    lat = models.DecimalField(max_digits=150, decimal_places=50)
    lon = models.DecimalField(max_digits=150, decimal_places=50)
    depth = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    mag = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    strike1 = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    dip1 = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    rake1 = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    strike2 = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    dip2 = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    rake2 = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    id = models.CharField(max_length=40, blank=True, null=True)
    location = models.CharField(max_length=120, blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'earthquakes'
        unique_together = (('date', 'lat', 'lon'),)


class EtmParams(BaseModel):
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode', max_length=4)
    soln = models.CharField(max_length=10)
    object = models.CharField(max_length=10)
    terms = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    frequencies = ArrayField(models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True))
    jump_type = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    relaxation = ArrayField(models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True))
    # Field name made lowercase.
    year = models.DecimalField(
        db_column='Year', max_digits=150, decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    doy = models.DecimalField(
        db_column='DOY', max_digits=150, decimal_places=50, blank=True, null=True)
    action = models.CharField(max_length=1, blank=True, null=True)
    uid = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'etm_params'


class Etms(BaseModel):
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode', max_length=4)
    soln = models.CharField(max_length=10)
    object = models.CharField(max_length=10)
    t_ref = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    jump_type = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    relaxation = ArrayField(models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True))
    frequencies = ArrayField(models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True))
    params = ArrayField(models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True))
    sigmas = ArrayField(models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True))
    metadata = models.TextField(blank=True, null=True)
    hash = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    jump_date = models.DateTimeField(blank=True, null=True)
    uid = models.AutoField(primary_key=True)
    stack = models.CharField(max_length=20, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'etms'


class Events(BaseModel):
    # The composite primary key (event_id, EventDate) found, that is not supported. The first column is selected.
    event_id = models.BigAutoField(primary_key=True)
    # Field name made lowercase.
    event_date = models.DateTimeField(
        default=lambda: datetime.datetime.now(datetime.timezone.utc), db_column='EventDate')
    # Field name made lowercase.
    event_type = models.CharField(
        db_column='EventType', max_length=6, blank=True, null=True)
    # Field name made lowercase.
    network_code = models.CharField(
        db_column='NetworkCode', max_length=3, blank=True, null=True)
    # Field name made lowercase.
    station_code = models.CharField(
        db_column='StationCode', max_length=4, blank=True, null=True)
    # Field name made lowercase.
    year = models.IntegerField(db_column='Year', blank=True, null=True)
    # Field name made lowercase.
    doy = models.IntegerField(db_column='DOY', blank=True, null=True)
    # Field name made lowercase.
    description = models.TextField(
        db_column='Description', blank=True, null=True)
    stack = models.TextField(blank=True, null=True)
    module = models.TextField(blank=True, null=True)
    node = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'events'
        ordering = ["-event_date"]
        unique_together = (('event_id', 'event_date'),)


class Executions(BaseModel):
    script = models.CharField(max_length=40, blank=True, null=True)
    exec_date = models.DateTimeField(blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'executions'


class GamitHtc(BaseModel):
    # Field name made lowercase. The composite primary key (AntennaCode, HeightCode) found, that is not supported. The first column is selected.
    antenna_code = models.CharField(db_column='AntennaCode', max_length=22)
    # Field name made lowercase.
    height_code = models.CharField(db_column='HeightCode', max_length=5)
    v_offset = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    h_offset = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)

    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'gamit_htc'
        ordering = ["antenna_code", "height_code"]
        unique_together = (('antenna_code', 'height_code'),)


class GamitSoln(BaseModel):
    # Field name made lowercase.
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode', max_length=4)
    # Field name made lowercase.
    project = models.CharField(db_column='Project', max_length=20)
    # Field name made lowercase.
    year = models.DecimalField(
        db_column='Year', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    doy = models.DecimalField(
        db_column='DOY', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    fyear = models.DecimalField(
        db_column='FYear', max_digits=150, decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    x = models.DecimalField(db_column='X', max_digits=150,
                            decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    y = models.DecimalField(db_column='Y', max_digits=150,
                            decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    z = models.DecimalField(db_column='Z', max_digits=150,
                            decimal_places=50, blank=True, null=True)
    sigmax = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmay = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmaz = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    variance_factor = models.DecimalField(
        db_column='VarianceFactor', max_digits=150, decimal_places=50, blank=True, null=True)
    sigmaxy = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmayz = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmaxz = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'gamit_soln'
        unique_together = (
            ('network_code', 'station_code', 'project', 'year', 'doy'),)


class GamitSolnExcl(BaseModel):
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode', max_length=4)
    # Field name made lowercase.
    project = models.CharField(db_column='Project', max_length=20)
    # Field name made lowercase.
    year = models.BigIntegerField(db_column='Year')
    doy = models.BigIntegerField(db_column='DOY')  # Field name made lowercase.
    residual = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'gamit_soln_excl'
        unique_together = (
            ('network_code', 'station_code', 'project', 'year', 'doy'),)


class GamitStats(BaseModel):
    # Field name made lowercase. The composite primary key (Project, subnet, Year, DOY, system) found, that is not supported. The first column is selected.
    project = models.CharField(
        db_column='Project', max_length=20)
    subnet = models.DecimalField(max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    year = models.DecimalField(
        db_column='Year', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    doy = models.DecimalField(
        db_column='DOY', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    f_year = models.DecimalField(
        db_column='FYear', max_digits=150, decimal_places=50, blank=True, null=True)
    wl = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    nl = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    nrms = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    relaxed_constrains = models.TextField(blank=True, null=True)
    max_overconstrained = models.CharField(max_length=8, blank=True, null=True)
    updated_apr = models.TextField(blank=True, null=True)
    iterations = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    node = models.CharField(max_length=50, blank=True, null=True)
    execution_time = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    execution_date = models.DateTimeField(blank=True, null=True)
    system = models.CharField(max_length=1)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'gamit_stats'
        unique_together = (('project', 'subnet', 'year', 'doy', 'system'),)


class GamitSubnets(BaseModel):
    # Field name made lowercase. The composite primary key (Project, subnet, Year, DOY) found, that is not supported. The first column is selected.
    project = models.CharField(
        db_column='Project', max_length=20)
    subnet = models.DecimalField(max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    year = models.DecimalField(
        db_column='Year', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    doy = models.DecimalField(
        db_column='DOY', max_digits=65535, decimal_places=65535)
    centroid = ArrayField(models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True))
    stations = ArrayField(models.CharField())
    alias = ArrayField(models.CharField())
    ties = ArrayField(models.CharField())
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'gamit_subnets'
        unique_together = (('project', 'subnet', 'year', 'doy'),)


class GamitZtd(BaseModel):
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode', max_length=4)
    date = models.DateTimeField(db_column='Date')  # Field name made lowercase.
    # Field name made lowercase.
    project = models.CharField(db_column='Project', max_length=20)
    # Field name made lowercase.
    year = models.DecimalField(
        db_column='Year', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    doy = models.DecimalField(
        db_column='DOY', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    ztd = models.DecimalField(
        db_column='ZTD', max_digits=65535, decimal_places=65535)
    model = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigma = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'gamit_ztd'
        unique_together = (('network_code', 'station_code',
                           'date', 'project', 'year', 'doy'),)


class Keys(BaseModel):
    # Field name made lowercase.
    key_code = models.CharField(
        db_column='KeyCode', max_length=7)
    # Field name made lowercase.
    total_chars = models.IntegerField(
        db_column='TotalChars', blank=True, null=True)
    rinex_col_out = models.CharField(blank=True, null=True)
    rinex_col_in = models.CharField(max_length=60, blank=True, null=True)
    isnumeric = custom_fields.BitField(null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'keys'


class Locks(BaseModel):
    filename = models.TextField()
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(
        db_column='StationCode', max_length=4, blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'locks'


class Networks(BaseModel):
    # Field name made lowercase.
    network_code = models.CharField(db_column='NetworkCode', unique=True)
    # Field name made lowercase.
    network_name = models.CharField(
        db_column='NetworkName', blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'networks'


class PppSoln(BaseModel):
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode')
    # Field name made lowercase.
    x = models.DecimalField(db_column='X', max_digits=12,
                            decimal_places=4, blank=True, null=True)
    # Field name made lowercase.
    y = models.DecimalField(db_column='Y', max_digits=12,
                            decimal_places=4, blank=True, null=True)
    # Field name made lowercase.
    z = models.DecimalField(db_column='Z', max_digits=12,
                            decimal_places=4, blank=True, null=True)
    # Field name made lowercase.
    year = models.DecimalField(
        db_column='Year', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    doy = models.DecimalField(
        db_column='DOY', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    reference_frame = models.CharField(
        db_column='ReferenceFrame', max_length=20)
    sigmax = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmay = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmaz = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmaxy = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmaxz = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmayz = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    hash = models.IntegerField(blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'ppp_soln'
        unique_together = (('network_code', 'station_code',
                           'year', 'doy', 'reference_frame'),)


class PppSolnExcl(BaseModel):
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode', max_length=4)
    # Field name made lowercase.
    year = models.DecimalField(
        db_column='Year', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    doy = models.DecimalField(
        db_column='DOY', max_digits=65535, decimal_places=65535)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'ppp_soln_excl'
        unique_together = (('network_code', 'station_code', 'year', 'doy'),)


class Receivers(BaseModel):
    # Field name made lowercase.
    receiver_code = models.CharField(
        db_column='ReceiverCode', max_length=22)
    # Field name made lowercase.
    receiver_description = models.CharField(
        db_column='ReceiverDescription', max_length=22, blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        ordering = ["receiver_code"]
        db_table = 'receivers'


class Rinex(BaseModel):
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode')
    # Field name made lowercase.
    observation_year = models.DecimalField(
        db_column='ObservationYear', max_digits=150, decimal_places=50)
    # Field name made lowercase.
    observation_month = models.DecimalField(
        db_column='ObservationMonth', max_digits=150, decimal_places=50)
    # Field name made lowercase.
    observation_day = models.DecimalField(
        db_column='ObservationDay', max_digits=150, decimal_places=50)
    # Field name made lowercase.
    observation_doy = models.DecimalField(
        db_column='ObservationDOY', max_digits=150, decimal_places=50)
    # Field name made lowercase.
    observation_f_year = models.DecimalField(
        db_column='ObservationFYear', max_digits=150, decimal_places=50)
    # Field name made lowercase.
    observation_s_time = models.DateTimeField(
        db_column='ObservationSTime', blank=True, null=True)
    # Field name made lowercase.
    observation_e_time = models.DateTimeField(
        db_column='ObservationETime', blank=True, null=True)
    # Field name made lowercase.
    receiver_type = models.CharField(
        db_column='ReceiverType', max_length=20, blank=True, null=True)
    # Field name made lowercase.
    receiver_serial = models.CharField(
        db_column='ReceiverSerial', max_length=20, blank=True, null=True)
    # Field name made lowercase.
    receiver_fw = models.CharField(
        db_column='ReceiverFw', max_length=20, blank=True, null=True)
    # Field name made lowercase.
    antenna_type = models.CharField(
        db_column='AntennaType', max_length=20, blank=True, null=True)
    # Field name made lowercase.
    antenna_serial = models.CharField(
        db_column='AntennaSerial', max_length=20, blank=True, null=True)
    # Field name made lowercase.
    antenna_dome = models.CharField(
        db_column='AntennaDome', max_length=20, blank=True, null=True)
    # Field name made lowercase.
    filename = models.CharField(
        db_column='Filename', max_length=50, blank=True, null=True)
    # Field name made lowercase.
    interval = models.DecimalField(
        db_column='Interval', max_digits=150, decimal_places=50)
    # Field name made lowercase.
    antenna_offset = models.DecimalField(
        db_column='AntennaOffset', max_digits=150, decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    completion = models.DecimalField(
        db_column='Completion', max_digits=7, decimal_places=3)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'rinex'
        ordering = ["observation_s_time"]
        unique_together = (('network_code', 'station_code', 'observation_year',
                           'observation_doy', 'interval', 'completion'),)


class RinexSourcesInfo(BaseModel):
    name = models.CharField(max_length=20)
    fqdn = models.CharField()
    protocol = models.CharField()
    username = models.CharField(blank=True, null=True)
    password = models.CharField(blank=True, null=True)
    path = models.CharField(blank=True, null=True)
    format = models.CharField(blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'rinex_sources_info'


class RinexTankStruct(BaseModel):
    # Field name made lowercase.
    level = models.IntegerField(db_column='Level')
    # Field name made lowercase.
    key_code = models.ForeignKey(
        Keys, models.DO_NOTHING, db_column='KeyCode', blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'rinex_tank_struct'


class SourcesFormats(BaseModel):
    format = models.CharField(unique=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'sources_formats'


class SourcesServers(BaseModel):
    server_id = models.AutoField(primary_key=True)
    protocol = models.CharField()
    fqdn = models.CharField()
    username = models.CharField(blank=True, null=True)
    password = models.CharField(blank=True, null=True)
    path = models.CharField(blank=True, null=True)
    format = models.ForeignKey(
        SourcesFormats, models.DO_NOTHING, db_column='format', to_field='format')

    class Meta:
        managed = False
        db_table = 'sources_servers'
        ordering = ["fqdn"]


class SourcesStations(BaseModel):
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    station_code = models.CharField(db_column='StationCode', max_length=4)
    try_order = models.SmallIntegerField()
    server_id = models.ForeignKey(
        SourcesServers, models.DO_NOTHING, to_field='server_id', db_column='server_id')
    path = models.CharField(blank=True, null=True)
    format = models.ForeignKey(
        SourcesFormats, models.DO_NOTHING, db_column='format', to_field='format', blank=True, null=True)

    api_id = models.AutoField(primary_key=True)

    def save(self, *args, **kwargs):
        if self.path == '':
            self.path = None
        if self.format == '':
            self.format = None
        super().save(*args, **kwargs)

    class Meta:
        managed = False
        db_table = 'sources_stations'
        unique_together = (('network_code', 'station_code', 'try_order'),)


class Stacks(BaseModel):
    # Field name made lowercase. The composite primary key (NetworkCode, StationCode, Year, DOY, name) found, that is not supported. The first column is selected.
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode', max_length=4)
    # Field name made lowercase.
    project = models.CharField(db_column='Project', max_length=20)
    # Field name made lowercase.
    year = models.DecimalField(
        db_column='Year', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    doy = models.DecimalField(
        db_column='DOY', max_digits=65535, decimal_places=65535)
    # Field name made lowercase.
    f_year = models.DecimalField(
        db_column='FYear', max_digits=150, decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    x = models.DecimalField(db_column='X', max_digits=150,
                            decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    y = models.DecimalField(db_column='Y', max_digits=150,
                            decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    z = models.DecimalField(db_column='Z', max_digits=150,
                            decimal_places=50, blank=True, null=True)
    sigmax = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmay = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmaz = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    variance_factor = models.DecimalField(
        db_column='VarianceFactor', max_digits=150, decimal_places=50, blank=True, null=True)
    sigmaxy = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmayz = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    sigmaxz = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    name = models.CharField(max_length=20)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'stacks'
        ordering = ["name"]
        unique_together = (
            ('network_code', 'station_code', 'year', 'doy', 'name'),)


class Stationalias(BaseModel):
    # Field name made lowercase.
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode', max_length=4)
    # Field name made lowercase.
    station_alias = models.CharField(
        db_column='StationAlias', unique=True, max_length=4)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'stationalias'
        unique_together = (('network_code', 'station_code'),)


class Stationinfo(BaseModel):
    # Field name made lowercase.
    network_code = models.CharField(db_column='NetworkCode', max_length=3)
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode', max_length=4)
    # Field name made lowercase.
    receiver_code = models.CharField(
        db_column='ReceiverCode', max_length=22)
    # Field name made lowercase.
    receiver_serial = models.CharField(
        db_column='ReceiverSerial', max_length=22, blank=True, null=True)
    # Field name made lowercase.
    receiver_firmware = models.CharField(
        db_column='ReceiverFirmware', max_length=10, blank=True, null=True)
    # Field name made lowercase.
    antenna_code = models.CharField(
        db_column='AntennaCode', max_length=22)
    # Field name made lowercase.
    antenna_serial = models.CharField(
        db_column='AntennaSerial', max_length=20, blank=True, null=True)
    # Field name made lowercase.
    antenna_height = models.DecimalField(
        db_column='AntennaHeight', max_digits=6, decimal_places=4, blank=True, null=True)
    # Field name made lowercase.
    antenna_north = models.DecimalField(
        db_column='AntennaNorth', max_digits=12, decimal_places=4, blank=True, null=True)
    # Field name made lowercase.
    antenna_east = models.DecimalField(
        db_column='AntennaEast', max_digits=12, decimal_places=4, blank=True, null=True)
    # Field name made lowercase.
    height_code = models.CharField(
        db_column='HeightCode')
    # Field name made lowercase.
    radome_code = models.CharField(db_column='RadomeCode', max_length=7)
    # Field name made lowercase.
    date_start = models.DateTimeField(db_column='DateStart')
    # Field name made lowercase.
    date_end = models.DateTimeField(db_column='DateEnd', blank=True, null=True)
    # Field name made lowercase.
    receiver_vers = models.CharField(
        db_column='ReceiverVers', max_length=22, blank=True, null=True)
    # Field name made lowercase.
    comments = models.TextField(db_column='Comments', blank=True, null=True)
    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'stationinfo'
        ordering = ["date_start"]
        constraints = [
            models.UniqueConstraint(
                fields=['network_code', 'station_code', 'date_start'], name='network_code_station_code_date_start_unique')
        ]


class Stations(BaseModel):
    # Field name made lowercase.
    network_code = models.ForeignKey(
        Networks, models.DO_NOTHING, db_column='NetworkCode', to_field="network_code")
    # Field name made lowercase.
    station_code = models.CharField(db_column='StationCode', max_length=4)
    # Field name made lowercase.
    station_name = models.CharField(
        db_column='StationName', max_length=40, blank=True, null=True)
    # Field name made lowercase.
    date_start = models.DecimalField(
        db_column='DateStart', max_digits=7, decimal_places=3, blank=True, null=True)
    # Field name made lowercase.
    date_end = models.DecimalField(
        db_column='DateEnd', max_digits=7, decimal_places=3, blank=True, null=True)
    auto_x = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    auto_y = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    auto_z = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    # Field name made lowercase.
    harpos_coeff_otl = models.TextField(
        db_column='Harpos_coeff_otl', blank=True, null=True)
    lat = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    lon = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    height = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    max_dist = models.DecimalField(
        max_digits=150, decimal_places=50, blank=True, null=True)
    dome = models.CharField(max_length=9, blank=True, null=True)
    country_code = models.CharField(max_length=3, blank=True, null=True)
    marker = models.IntegerField(blank=True, null=True)

    api_id = models.AutoField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'stations'
        ordering = ["api_id"]
        unique_together = (('network_code', 'station_code'),
                           ('network_code', 'station_code'),)
# ----------------------------------API-SPECIFIC MODELS-------------------------------------


def station_attached_files_path(instance, filename):
    return os.path.join("stations", f"{instance.station.network_code.network_code}", f"{instance.station.station_code}", "attached_files", filename)


def get_image_name(instance, filename):
    # Save with specified name if provided. Save with image name otherwise.
    if hasattr(instance, 'name') and isinstance(instance.name, str) and instance.name != "":
        return instance.name
    else:
        return filename


def station_images_path(instance, filename):

    return os.path.join("stations", f"{instance.station.network_code.network_code}", f"{instance.station.station_code}", "images", get_image_name(instance, filename))


def station_log_sheet_file_path(instance, filename):
    return os.path.join("stations", f"{instance.station.network_code.network_code}", f"{instance.station.station_code}", "log_sheet_file", filename)


def station_navigation_file_path(instance, filename):
    return os.path.join("stations", f"{instance.station.network_code.network_code}", f"{instance.station.station_code}", "navigation_file", filename)


def visits_attached_files_path(instance, filename):
    return os.path.join("stations", f"{instance.visit.station.network_code.network_code}", f"{instance.visit.station.station_code}", "visits", f"{instance.visit.date}", "attached_files", filename)


def visits_images_path(instance, filename):
    return os.path.join("stations", f"{instance.visit.station.network_code.network_code}", f"{instance.visit.station.station_code}", "visits", f"{instance.visit.date}", "images", get_image_name(instance, filename))


def visits_log_sheet_file_path(instance, filename):
    return os.path.join("stations", f"{instance.station.network_code.network_code}", f"{instance.station.station_code}", "visits", f"{instance.date}", "log_sheet_file", filename)


def visits_navigation_file_path(instance, filename):
    return os.path.join("stations", f"{instance.station.network_code.network_code}", f"{instance.station.station_code}", "visits", f"{instance.date}", "navigation_file", filename)


def visits_gnss_data_files_path(instance, filename):
    return os.path.join("stations", f"{instance.visit.station.network_code.network_code}", f"{instance.visit.station.station_code}", "visits", f"{instance.visit.date}", "gnss_data_files", filename)


class Country(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    two_digits_code = models.CharField(max_length=2, unique=True)
    three_digits_code = models.CharField(max_length=3, unique=True)

    def __str__(self):
        return self.name


class Endpoint(BaseModel):
    path = models.CharField(max_length=100)
    description = models.CharField(max_length=100, blank=True)

    METHOD_CHOICES = [
        ("GET", "GET"),
        ("POST", "POST"),
        ("PUT", "PUT"),
        ("DELETE", "DELETE"),
        ("PATCH", "PATCH"),
        ("ALL", "ALL")
    ]

    method = models.CharField(max_length=6, choices=METHOD_CHOICES)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['path', 'method'], name='path_method_unique')
        ]

    def __str__(self):
        return self.path


class ClusterType(BaseModel):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Resource(BaseModel):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class EndPointsCluster(BaseModel):
    resource = models.ForeignKey(Resource, models.CASCADE)
    description = models.CharField(max_length=100, blank=True)
    endpoints = models.ManyToManyField(Endpoint)
    cluster_type = models.ForeignKey(ClusterType, models.CASCADE)

    ROLE_TYPE_CHOICES = [("FRONT", "FRONT"), ("API", "API"),
                         ("FRONT AND API", "FRONT AND API")]

    role_type = models.CharField(
        max_length=15,
        choices=ROLE_TYPE_CHOICES,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['resource', 'cluster_type', 'role_type'], name='resource_cluster_type_role_type_unique')
        ]


class Role(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    role_api = models.BooleanField()
    allow_all = models.BooleanField()
    endpoints_clusters = models.ManyToManyField(EndPointsCluster, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class User(AbstractUser):
    role = models.ForeignKey(
        Role, models.CASCADE)
    phone = models.CharField(max_length=15, blank=True)
    address = models.CharField(max_length=100, blank=True)
    photo = models.ImageField(upload_to='user_photos/', blank=True)
    clustering_distance = models.IntegerField(default=10)

    REQUIRED_FIELDS = ["role"]

    def __str__(self):
        return self.username


class StationRole(BaseModel):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Person(BaseModel):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(max_length=100, blank=True)
    phone = models.CharField(max_length=15, blank=True)
    address = models.CharField(max_length=100, blank=True)
    photo = models.ImageField(upload_to='person_photos/', blank=True)
    user = models.OneToOneField(User, models.SET_NULL, blank=True, null=True)
    institution = models.CharField(max_length=100, blank=True)
    position = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.first_name + ' ' + self.last_name

    class Meta:
        ordering = ["last_name", "first_name"]


class RolePersonStation(BaseModel):
    role = models.ForeignKey(StationRole, models.CASCADE)
    person = models.ForeignKey(Person, models.CASCADE)
    station = models.ForeignKey(Stations, models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['role', 'person', 'station'], name='role_person_station_unique')
        ]


class StationImages(BaseModel):
    station = models.ForeignKey(Stations, models.CASCADE)
    image = models.ImageField(upload_to=station_images_path)
    name = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['station', 'name'], name='station_name_unique')
        ]
        ordering = ["name"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.image and hasattr(self.image, 'path'):
            utils.FilesUtils.set_file_ownership(
                self.image.path, settings.USER_ID_TO_SAVE_FILES, settings.GROUP_ID_TO_SAVE_FILES)


class StationStatusColor(BaseModel):
    color = models.CharField(max_length=50, unique=True, default="green-icon")

    def __str__(self):
        return self.color


class StationStatus(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    color = models.ForeignKey(StationStatusColor, models.PROTECT)

    def __str__(self):
        return self.name


class MonumentType(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    photo_path = models.ImageField(
        upload_to='monument_type_photos/', blank=True)

    def __str__(self):
        return self.name


class StationType(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    # only true for station types created at the beginning
    search_icon_on_assets_folder = models.BooleanField(default=False)
    icon = models.ImageField(
        upload_to='station_type_icons/')

    def get_icon_url(self):
        if self.search_icon_on_assets_folder:
            return os.path.join(settings.ASSETS_FOLDER, self.icon.name)
        return os.path.join(settings.MEDIA_ROOT, self.icon.name)

    def __str__(self):
        return self.name


class StationAttachedFiles(BaseModel):
    station = models.ForeignKey(Stations, models.CASCADE)
    file = models.FileField(upload_to=station_attached_files_path)
    filename = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['station', 'filename'], name='station_filename_unique')
        ]
        ordering = ["filename"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        # Then set ownership on the file and directories
        if self.file and hasattr(self.file, 'path'):
            utils.FilesUtils.set_file_ownership(
                self.file.path, settings.USER_ID_TO_SAVE_FILES, settings.GROUP_ID_TO_SAVE_FILES)


class StationMeta(BaseModel):
    station = models.ForeignKey(Stations, models.CASCADE)
    status = models.ForeignKey(
        StationStatus, models.SET_NULL, blank=True, null=True)
    monument_type = models.ForeignKey(
        MonumentType, models.SET_NULL, blank=True, null=True)
    remote_access_link = models.CharField(max_length=500, blank=True)
    has_battery = models.BooleanField(default=False)
    battery_description = models.CharField(max_length=100, blank=True)
    has_communications = models.BooleanField(default=False)
    communications_description = models.CharField(max_length=100, blank=True)
    station_type = models.ForeignKey(
        StationType, models.SET_NULL, blank=True, null=True)
    comments = models.CharField(blank=True)
    navigation_file = models.FileField(
        upload_to=station_navigation_file_path, blank=True)
    navigation_filename = models.CharField(max_length=255, blank=True)
    has_gaps = models.BooleanField(default=False)
    has_gaps_last_update_datetime = models.DateTimeField(blank=True, null=True)
    has_gaps_update_needed = models.BooleanField(default=True)
    has_stationinfo = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['station'], name='station_unique')
        ]


class StationMetaGaps(BaseModel):
    station_meta = models.ForeignKey(StationMeta, models.CASCADE)
    rinex_count = models.IntegerField()
    record_start_date_start = models.DateTimeField(blank=True, null=True)
    record_start_date_end = models.DateTimeField(blank=True, null=True)
    record_end_date_start = models.DateTimeField(blank=True, null=True)
    record_end_date_end = models.DateTimeField(blank=True, null=True)

    def to_dict(self):
        return {field.name: getattr(self, field.name) for field in self._meta.fields}


class Campaigns(BaseModel):
    name = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    default_people = models.ManyToManyField(
        Person, blank=True)

    class Meta:
        ordering = ["-start_date"]


class Visits(BaseModel):
    date = models.DateField()
    campaign = models.ForeignKey(
        Campaigns, models.SET_NULL, blank=True, null=True)
    station = models.ForeignKey(Stations, models.CASCADE)
    people = models.ManyToManyField(Person, blank=True)
    log_sheet_file = models.FileField(
        upload_to=visits_log_sheet_file_path, blank=True, null=True)
    log_sheet_filename = models.CharField(max_length=255, blank=True)
    navigation_file = models.FileField(
        upload_to=visits_navigation_file_path, blank=True)
    navigation_filename = models.CharField(max_length=255, blank=True)
    comments = models.CharField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['station', 'date'], name='station_date_unique')
        ]
        ordering = ["-date"]


class VisitImages(BaseModel):
    visit = models.ForeignKey(Visits, models.CASCADE)
    image = models.ImageField(upload_to=visits_images_path)
    name = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['visit', 'name'], name='visit_name_unique')
        ]
        ordering = ["name"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.image and hasattr(self.image, 'path'):
            utils.FilesUtils.set_file_ownership(
                self.image.path, settings.USER_ID_TO_SAVE_FILES, settings.GROUP_ID_TO_SAVE_FILES)


class VisitAttachedFiles(BaseModel):
    visit = models.ForeignKey(Visits, models.CASCADE)
    file = models.FileField(upload_to=visits_attached_files_path)
    filename = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['visit', 'filename'], name='visit_filename_unique')
        ]
        ordering = ["filename"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.file and hasattr(self.file, 'path'):
            utils.FilesUtils.set_file_ownership(
                self.file.path, settings.USER_ID_TO_SAVE_FILES, settings.GROUP_ID_TO_SAVE_FILES)


class VisitGNSSDataFiles(BaseModel):
    visit = models.ForeignKey(Visits, models.CASCADE)
    file = models.FileField(upload_to=visits_gnss_data_files_path)
    filename = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=500, blank=True)
    size = models.CharField(max_length=20, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['visit', 'filename'], name='visit_filename_gnss_unique')
        ]
        ordering = ["filename"]

    def save(self, *args, **kwargs):
        # Save file size before calling super().save()
        if self.file and hasattr(self.file, 'size'):
            # Convert bytes to human readable format
            size = self.file.size
            if size < 1024:
                self.size = f"{size} B"
            elif size < 1024 * 1024:
                self.size = f"{size / 1024:.1f} KB"
            elif size < 1024 * 1024 * 1024:
                self.size = f"{size / (1024 * 1024):.1f} MB"
            else:
                self.size = f"{size / (1024 * 1024 * 1024):.1f} GB"

        super().save(*args, **kwargs)

        if self.file and hasattr(self.file, 'path'):
            utils.FilesUtils.set_file_ownership(
                self.file.path, settings.USER_ID_TO_SAVE_FILES, settings.GROUP_ID_TO_SAVE_FILES)


def enable_automatic_auditlog():
    auditlog.register(Antennas)
    auditlog.register(Networks)
    auditlog.register(Person)
    auditlog.register(Receivers)
    auditlog.register(Rinex)
    auditlog.register(Stationinfo)
    auditlog.register(Stations)
    auditlog.register(Role)
    auditlog.register(Resource)
    auditlog.register(ClusterType)
    auditlog.register(User)
    auditlog.register(Endpoint)
    auditlog.register(RolePersonStation)
    auditlog.register(StationMeta)
    auditlog.register(EndPointsCluster)
    auditlog.register(StationAttachedFiles)
    auditlog.register(StationImages)
    auditlog.register(SourcesServers)
    auditlog.register(SourcesStations)
    auditlog.register(Campaigns)
    auditlog.register(Visits)
    auditlog.register(VisitImages)
    auditlog.register(VisitAttachedFiles)
    auditlog.register(VisitGNSSDataFiles)


enable_automatic_auditlog()

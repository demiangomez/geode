import datetime
from . import models
from . import exceptions
import numpy
import math
from django.db import connection
import django.utils.timezone
import logging
from django.core.cache import caches
from django.conf import settings
from django.forms.models import model_to_dict
from rest_framework.response import Response
from rest_framework import status
import gzip
from django.core.files.base import ContentFile
import re
import base64
from io import BytesIO
from PIL import Image, ImageOps
import grp
import os
from lxml import etree
import zipfile
from pgamit import pyOkada, dbConnection
from pgamit import pyStationInfo, dbConnection, pyDate, pyETM
from pgamit import Utils as pyUtils
import dateutil.parser
from django.http import Http404
import matplotlib.pyplot as plt
import time


from django.db import transaction
from django.db.models import Max

logger = logging.getLogger('django')


def get_actual_image(image_obj, request):
    """Returns the actual image encoded in base64, optionally as a thumbnail"""
    thumbnail = request.query_params.get(
        'thumbnail', 'false').lower() == 'true' if request else False
    original_quality = request.query_params.get(
        'original_quality', 'false').lower() == 'true' if request else False

    if image_obj and image_obj.name:
        try:
            with open(image_obj.path, 'rb') as photo_file:
                image_data = photo_file.read()
                if thumbnail:
                    image = Image.open(BytesIO(image_data))
                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((400, 400))
                    buffer = BytesIO()
                    if image.mode in ("RGBA", "P"):
                        image = image.convert("RGB")
                    image.save(buffer, format="JPEG")
                    image_data = buffer.getvalue()
                elif not original_quality:
                    image = Image.open(BytesIO(image_data))
                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((1000, 1000))
                    buffer = BytesIO()
                    if image.mode in ("RGBA", "P"):
                        image = image.convert("RGB")
                    image.save(buffer, format="JPEG")
                    image_data = buffer.getvalue()
                return base64.b64encode(image_data).decode('utf-8')
        except FileNotFoundError:
            return None
    else:
        return None


class StationUtils:
    @staticmethod
    def get_station_meta_info(stations):
        station_meta_query = models.StationMeta.objects.values_list(
            'station', 'has_gaps', 'has_stationinfo', 'id', 'status__name', 'station_type__name')

        station_meta_gaps = models.StationMetaGaps.objects.select_related(
            'station_meta').all()

        station_meta_gaps_dict = {station_meta_object[3]: [
        ] for station_meta_object in station_meta_query}

        for gap in station_meta_gaps:
            station_meta_gaps_dict[gap.station_meta.id].append(
                model_to_dict(gap))

        station_meta_dict = {
            station_meta_object[0]: (
                station_meta_object[1],
                station_meta_object[2],
                station_meta_object[3],
                station_meta_object[4],
                station_meta_object[5]
            )
            for station_meta_object in station_meta_query
        }

        for station in stations:
            if 'api_id' in station:
                if station['api_id'] in station_meta_dict:
                    has_gaps, has_stationinfo, station_meta_id, station_status_name, station_type_name = station_meta_dict[
                        station['api_id']]
                    station["has_gaps"] = has_gaps
                    station["has_stationinfo"] = has_stationinfo
                    station["gaps"] = station_meta_gaps_dict[station_meta_id]
                    station["status"] = station_status_name
                    station["type"] = station_type_name

    @staticmethod
    def parse_harpos_coeff_otl_file(file, network_code, station_code):

        file_content = file.read()
        file_as_string = file_content.decode('utf-8')
        harpos_parsed = pyUtils.import_blq(
            file_as_string, network_code, station_code)

        if len(harpos_parsed) > 0 and 'otl' in harpos_parsed[0]:
            harpos_parsed = harpos_parsed[0]['otl']

        return harpos_parsed


class TimeSeriesConfigUtils:
    def _get_required_param(self, request, params, param_name):
        param_value = request.query_params.get(param_name)

        # param_value can't be None or an empty string
        if param_value is not None and (isinstance(param_value, str) and param_value != ""):
            params[param_name] = param_value
        else:
            raise exceptions.CustomValidationErrorExceptionHandler(
                "'" + param_name + "'" + " parameter is required.")

    def _convert_to_bool(self, params, param_name):
        param_value = params[param_name].strip().lower()

        if param_value in ("true", "false"):
            params[param_name] = param_value == "true"
        else:
            raise exceptions.CustomValidationErrorExceptionHandler(
                "'" + param_name + "'" + " parameter is not a valid boolean.")

    def _check_params(self, request):
        params = {}

        for param_name in ("solution", "residuals", "missing_data", "plot_outliers", "plot_auto_jumps",
                           "no_model", "remove_jumps", "remove_polynomial"):
            self._get_required_param(request, params, param_name)

        self._get_dates_param(request, params)

        for param_name in ("residuals", "missing_data", "plot_outliers", "plot_auto_jumps", "no_model", "remove_jumps", "remove_polynomial"):
            self._convert_to_bool(params, param_name)

        params["solution"] = params["solution"].strip().upper()

        if params["solution"] not in ("PPP", "GAMIT"):
            raise exceptions.CustomValidationErrorExceptionHandler(
                "'solution' parameter must be either 'PPP' or 'GAMIT'")

        if params["solution"] == "GAMIT":
            self._get_required_param(request, params, "stack")

        return params

    def _deprecated_check_params(self, request, params):

        date_start = request.query_params.get("date_start")
        date_end = request.query_params.get("date_end")

        for param_name, param_value in (("date_start", date_start), ("date_end", date_end)):

            if param_value is not None:
                try:
                    date = dateutil.parser.parse(param_value)
                except (dateutil.parser.ParserError, ValueError):
                    raise exceptions.CustomValidationErrorExceptionHandler(
                        "'" + param_name + "'" + " parameter has a wrong format.")
                else:
                    params[param_name] = date
                    print(f"{type(date)=}")

        if isinstance(params["date_start"], datetime.datetime) and isinstance(params["date_end"], datetime.datetime):
            if params["date_start"] > params["date_end"]:
                raise exceptions.CustomValidationErrorExceptionHandler(
                    "'date_start' parameter can't be greater than 'date_end' parameter.")

    def _get_dates_param(self, request, params):
        date_start = request.query_params.get("date_start")
        date_end = request.query_params.get("date_end")

        time_window = []

        if date_start is not None:
            time_window.append(date_start)
        if date_end is not None:
            time_window.append(date_end)

        dates = None
        try:
            if len(time_window) > 0:
                if len(time_window) == 1:
                    try:
                        dates = pyUtils.process_date(
                            time_window, missing_input=None, allow_days=False)
                        dates = (dates[0].fyear, )
                    except ValueError:
                        # an integer value
                        dates = float(time_window[0])
                else:
                    dates = pyUtils.process_date(time_window)
                    dates = (dates[0].fyear, dates[1].fyear)
        except Exception as e:
            raise exceptions.CustomValidationErrorExceptionHandler(
                e.detail if hasattr(e, 'detail') else str(e))

        params["dates"] = dates

    def _get_station(self, station_api_id):
        try:
            station = models.Stations.objects.get(api_id=station_api_id)
        except models.Stations.DoesNotExist:
            raise Http404
        else:
            return station.network_code.network_code, station.station_code

    def _set_default_params(self):
        params = {}

        params["solution"] = "PPP"

        for param_name in ("residuals", "missing_data", "plot_outliers", "plot_auto_jumps",
                           "no_model", "remove_jumps", "remove_polynomial"):
            params[param_name] = "false"

        return params

    def _check_one_param(self, request, param_name, params):
        param_value = request.query_params.get(param_name)

        if param_value is not None and (isinstance(param_value, str) and param_value != ""):
            params[param_name] = param_value
            return params
        else:
            raise exceptions.CustomValidationErrorExceptionHandler(
                "'" + param_name + "'" + " parameter is required.")

    def initialize_etm(self, request, solution, check_params, station_api_id):

        if solution not in ("PPP", "GAMIT"):
            raise exceptions.CustomValidationErrorExceptionHandler(
                "Invalid solution parameter.")

        network_code, station_code = self._get_station(station_api_id)

        params = self._set_default_params()

        if check_params:
            params = self._check_params(request)

        self.cnn = dbConnection.Cnn(settings.CONFIG_FILE_ABSOLUTE_PATH)

        try:

            if solution == "GAMIT":

                if not check_params:
                    params = self._check_one_param(request, "stack", params)

                polyhedrons = self.cnn.query_float('SELECT "X", "Y", "Z", "Year", "DOY" FROM stacks '
                                                   'WHERE "name" = \'%s\' AND "NetworkCode" = \'%s\' AND '
                                                   '"StationCode" = \'%s\' '
                                                   'ORDER BY "Year", "DOY", "NetworkCode", "StationCode"'
                                                   % (params["stack"], network_code, station_code))

                soln = pyETM.GamitSoln(
                    self.cnn, polyhedrons, network_code, station_code, params["stack"])

                etm = pyETM.GamitETM(self.cnn, network_code, station_code, False,
                                     params["no_model"], gamit_soln=soln, plot_remove_jumps=params["remove_jumps"],
                                     plot_polynomial_removed=params["remove_polynomial"])
            else:

                etm = pyETM.PPPETM(self.cnn, network_code, station_code, False, params["no_model"],
                                   plot_remove_jumps=params["remove_jumps"],
                                   plot_polynomial_removed=params["remove_polynomial"])

        except Exception as e:
            raise exceptions.CustomValidationErrorExceptionHandler(
                e.detail if hasattr(e, 'detail') else str(e))

        return etm

    def get_cnn(self):
        return self.cnn


class FilesUtils:
    @staticmethod
    def set_file_ownership(file_path, user_id, group_id):
        """Set proper ownership on file and ensure directories have correct ownership"""
        if not os.path.exists(file_path):
            return

        try:
            # Set ownership on the file
            user_id = int(user_id)
            group_id = int(group_id)
            os.chown(file_path, user_id, group_id)

            # Set ownership on all parent directories
            directory = os.path.dirname(file_path)
            FilesUtils._ensure_directory_ownership(
                directory, user_id, group_id)

        except Exception as e:
            # Log the error but don't prevent the save
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to set file ownership: {e}")

    @staticmethod
    def _ensure_directory_ownership(directory, uid, gid):
        """Recursively set ownership on directory and its parents"""
        # Get the app's media root to avoid going above it
        from django.conf import settings
        media_root = os.path.abspath(settings.MEDIA_ROOT)

        # Walk up the directory tree until media root
        current_dir = os.path.abspath(directory)
        while current_dir and os.path.exists(current_dir):
            # Don't go above media root for security
            if not current_dir.startswith(media_root):
                break

            try:
                os.chown(current_dir, uid, gid)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to set directory ownership: {e}")
                break

            # Move up to parent directory
            parent = os.path.dirname(current_dir)
            if parent == current_dir:  # Reached root
                break
            current_dir = parent


class PersonUtils:
    @staticmethod
    def get_relations(person):
        role_person_station = models.RolePersonStation.objects.filter(
            person=person)

        visits = models.Visits.objects.filter(people=person)

        return role_person_station, visits

    @staticmethod
    def merge_person(person_source, person_target):

        # transfer all visits
        visits = models.Visits.objects.filter(people__in=[person_source])
        for visit in visits:
            if person_target not in visit.people.all():
                visit.people.add(person_target)
            visit.people.remove(person_source)
            visit.save()

        # transfer all roles with stations
        role_person_stations_from_source = models.RolePersonStation.objects.filter(
            person=person_source)
        role_person_stations_from_target = models.RolePersonStation.objects.filter(
            person=person_target)
        for role_person_station_source in role_person_stations_from_source:
            # if same relation exists in target, remove source relation
            if role_person_stations_from_target.filter(station=role_person_station_source.station, role=role_person_station_source.role).count() == 0:
                role_person_station_source.person = person_target
                role_person_station_source.save()
            else:
                role_person_station_source.delete()

        # transfer default people in all campaign
        default_people = models.Campaigns.objects.filter(
            default_people__in=[person_source])

        for campaign in default_people:
            if person_target not in campaign.default_people.all():
                campaign.default_people.add(person_target)
            campaign.default_people.remove(person_source)
            campaign.save()


class SourceServerUtils:
    @staticmethod
    def merge_source_server(source_server_source, source_server_target):

        # transfer all source stations
        sources_stations = models.SourcesStations.objects.filter(
            server_id=source_server_source.server_id)

        for source_station in sources_stations:
            source_station.server_id = source_server_target
            source_station.save()

    @staticmethod
    @transaction.atomic
    def swap_try_order(source_station_from, source_station_to):
        # check that objects share the same network code and station code
        if source_station_from.network_code != source_station_to.network_code or source_station_from.station_code != source_station_to.station_code:
            raise exceptions.CustomValidationErrorExceptionHandler(
                "both sources_stations records must have the same network code and station code")

        # Store the original try_order values
        from_try_order = source_station_from.try_order
        to_try_order = source_station_to.try_order

        # Find a unique temporary try_order value
        max_try_order = models.SourcesStations.objects.filter(
            network_code=source_station_from.network_code,
            station_code=source_station_from.station_code
        ).aggregate(Max('try_order'))['try_order__max'] or 0
        temp_try_order = max_try_order + 100  # Use a value unlikely to conflict

        # Use a three-step swap to avoid constraint violations
        source_station_from.try_order = temp_try_order
        source_station_from.save()

        source_station_to.try_order = from_try_order
        source_station_to.save()

        source_station_from.try_order = to_try_order
        source_station_from.save()


class StationKMZGenerator:

    @staticmethod
    def get_visit_description(visit):
        # if visit has campaign
        if visit.campaign:
            return f'<li>{visit.date.strftime("%Y-%m-%d")} (CAMPAIGN NAME: {visit.campaign.name})</li>'
        else:
            return f'<li>{visit.date.strftime("%Y-%m-%d")} (NO CAMPAIGN)</li>'

    @staticmethod
    def generate_station_kmz(station):
        # check if station is of type models.Station
        if not isinstance(station, models.Stations):
            raise exceptions.CustomServerErrorExceptionHandler(
                "station must be an instance of models.Station")

        # get stationmeta from station
        station_meta = models.StationMeta.objects.filter(
            station=station).first()

        # Crear la estructura base del KML
        kml = etree.Element('kml', xmlns="http://www.opengis.net/kml/2.2")
        document = etree.SubElement(kml, 'Document')

        # Add the station icon style
        style = etree.SubElement(document, 'Style', id="stationIcon")
        icon_style = etree.SubElement(style, 'IconStyle')
        scale = etree.SubElement(icon_style, 'scale')
        scale.text = "1.0"
        icon = etree.SubElement(icon_style, 'Icon')
        icon_href = etree.SubElement(icon, 'href')
        icon_href.text = "files/icons/station.png"

        # Create a marker for the station with a balloon popup window with metadata (name)
        placemark_station = etree.SubElement(document, 'Placemark')
        style_url = etree.SubElement(placemark_station, 'styleUrl')
        style_url.text = "#stationIcon"
        name_station = etree.SubElement(placemark_station, 'name')
        name_station.text = station.network_code.network_code.upper() + "." + \
            station.station_code.upper()
        description = etree.SubElement(placemark_station, 'description')
        visit_dates = ''.join(
            [StationKMZGenerator.get_visit_description(visit) for visit in models.Visits.objects.filter(station=station)])

        # Display all images from StationImages in the balloon
        images_html = ""
        for images in models.StationImages.objects.filter(station=station):
            if images.image:
                try:
                    with open(images.image.path, 'rb') as img_file:
                        image_data = base64.b64encode(
                            img_file.read()).decode('utf-8')
                        images_html += f'<img src="data:image/jpeg;base64,{image_data}" width="400"/>'
                except FileNotFoundError:
                    logger.error(
                        f"Image file not found for image {images.name}")

        description.text = f"""
        <![CDATA[
            <html>
              <head><style>p {{ font-family: Arial, sans-serif; }}</style></head>
              <body>
            <h3>Station Name: {"-" if station.station_name is None else station.station_name}</h3>
            <h3>Monument Type: {"-" if station_meta.monument_type is None else station_meta.monument_type}</h3>
            <h3>Station Type: {"-" if station_meta.station_type is None else station_meta.station_type}</h3>
            <h3>Remote Access Link: {"-" if station_meta.remote_access_link is None else station_meta.remote_access_link}</h3>
            <h4>Visits:</h4>
            <ul>
                {visit_dates}
            </ul>
            <h3>Comments: {station_meta.comments}</h3>
            <h3>Station Images:</h3>
            {images_html}
              </body>
            </html>
            ]]>
        """

        # Añadir la estación como punto en el KML
        point = etree.SubElement(placemark_station, 'Point')
        coordinates = etree.SubElement(point, 'coordinates')
        coordinates.text = f"{station.lon},{station.lat},0"

        # Agregar el archivo KML de la ruta por defecto
        if station_meta.navigation_file:
            # Add the road as a NetworkLink
            networklink = etree.SubElement(document, 'NetworkLink')
            networklink.attrib['id'] = 'default_road'
            networklink_name = etree.SubElement(networklink, 'name')
            networklink_name.text = 'Default Road'
            link = etree.SubElement(networklink, 'Link')
            href = etree.SubElement(link, 'href')
            href.text = 'files/default_road.kml'

        # Add visits as folders and place the navigation_file as roads within /files/visits
        for visit in models.Visits.objects.filter(station=station):
            # Create a folder for the visit
            folder = etree.SubElement(document, 'Folder')
            folder_name = etree.SubElement(folder, 'name')
            folder_name.text = visit.date.strftime("%Y-%m-%d")

            # Add the road as a NetworkLink
            if visit.navigation_file:
                networklink = etree.SubElement(folder, 'NetworkLink')
                networklink.attrib['id'] = visit.date.strftime(
                    "%Y%m%d") + '_road'
                networklink_name = etree.SubElement(networklink, 'name')
                networklink_name.text = 'Available Road'
                link = etree.SubElement(networklink, 'Link')
                href = etree.SubElement(link, 'href')
                href.text = f'files/visits/{visit.date.strftime("%Y-%m-%d")}/available_road.kml'

        # Convert KML tree to string
        kml_str = etree.tostring(
            kml, pretty_print=True, xml_declaration=True, encoding="UTF-8")

        # Create KMZ file in memory using BytesIO and zipfile
        kmz_buffer = BytesIO()
        with zipfile.ZipFile(kmz_buffer, 'w', zipfile.ZIP_DEFLATED) as kmz_file:
            # Add the KML doc
            kmz_file.writestr('doc.kml', kml_str)

            # Add navigation file from station_meta if it exists
            if station_meta and station_meta.navigation_file:
                try:
                    with open(station_meta.navigation_file.path, 'rb') as nav_file:
                        kmz_file.writestr(
                            'files/default_road.kml', nav_file.read())
                except FileNotFoundError:
                    logger.error(
                        f"Navigation file not found for station {station.network_code.network_code}.{station.station_code}")

            # add visit navigation files
            for visit in models.Visits.objects.filter(station=station):
                if visit.navigation_file:
                    try:
                        with open(visit.navigation_file.path, 'rb') as nav_file:
                            kmz_file.writestr(
                                f'files/visits/{visit.date.strftime("%Y-%m-%d")}/available_road.kml', nav_file.read())
                    except FileNotFoundError:
                        logger.error(
                            f"Navigation file not found for visit {visit.date.strftime('%Y-%m-%d')}")

            # save station icon file
            with open(os.path.join(settings.BASE_DIR, 'assets', 'station_icon.png'), 'rb') as icon_file:
                kmz_file.writestr('files/icons/station.png', icon_file.read())

        kmz_buffer.seek(0)

        return base64.b64encode(kmz_buffer.getvalue()).decode('utf-8')


class NearbyStations:
    @staticmethod
    def get_nearby_stations(station, distance_km):
        # check if station is of type models.Station
        if not isinstance(station, models.Stations):
            raise exceptions.CustomServerErrorExceptionHandler(
                "station must be an instance of models.Station")

        # check if distance_km is a positive number
        if not isinstance(distance_km, (int, float)) or distance_km <= 0:
            raise exceptions.CustomValidationErrorExceptionHandler(
                "distance_km must be a positive number")

        # get nearby stations
        nearby_stations = models.Stations.objects.filter(
            lat__range=(float(station.lat) - distance_km / 111,
                        float(station.lat) + distance_km / 111),
            lon__range=(float(station.lon) - distance_km / (111 * numpy.cos(numpy.radians(float(station.lat)))),
                        float(station.lon) + distance_km / (111 * numpy.cos(numpy.radians(float(station.lat)))))
        ).exclude(api_id=station.api_id)

        return nearby_stations


class EarthquakeUtils:
    @staticmethod
    def get_affected_stations(earthquake):
        # check if earthquake if of type models.Earthquake
        if not isinstance(earthquake, models.Earthquakes):
            raise exceptions.CustomServerErrorExceptionHandler(
                "earthquake must be an instance of models.Earthquake")

        cnn = dbConnection.Cnn(settings.CONFIG_FILE_ABSOLUTE_PATH)

        eq_t = pyOkada.EarthquakeTable(cnn, earthquake.id)

        affected_stations_only_postseismic = [{"network_code": affected_station["NetworkCode"],
                                               "station_code": affected_station["StationCode"],
                                               "distance": affected_station["distance"], "azimuth": affected_station["azimuth"]} for affected_station in eq_t.p_stations]

        affected_stations_without_postseismic = [{"network_code": affected_station["NetworkCode"],
                                                  "station_code": affected_station["StationCode"],
                                                  "distance": affected_station["distance"], "azimuth": affected_station["azimuth"]} for affected_station in eq_t.c_stations]

        affected_stations_including_postseismic = affected_stations_without_postseismic + \
            affected_stations_only_postseismic

        mask = pyOkada.Mask(cnn, earthquake.id)

        kml_mask_including_postseismic = mask.save_masks(
            include_postseismic=True)
        kml_mask_without_postseismic = mask.save_masks(
            include_postseismic=False)

        kml_mask_including_postseismic = base64.b64encode(
            kml_mask_including_postseismic.encode('utf-8')).decode('utf-8')

        kml_mask_without_postseismic = base64.b64encode(
            kml_mask_without_postseismic.encode('utf-8')).decode('utf-8')

        coseismic_displacements_dict = {(displacement['NetworkCode'], displacement['StationCode']): displacement for displacement in eq_t.get_coseismic_displacements()}

        return affected_stations_including_postseismic, affected_stations_without_postseismic, kml_mask_including_postseismic, kml_mask_without_postseismic, coseismic_displacements_dict

    def get_stations_csv_list(earthquake, stations_including_postseismic_reduced, stations_without_postseismic_reduced, coseismic_displacements_dict):
        header = ["Network Code", "Station Code", "Latitude",
                  "Longitude", "Postseismic", "Coseismic", "Observation Availability", "North Displacement", "East Displacement", "Up Displacement", "Distance", "Azimuth"]

        earthquake_date = earthquake.date.date()
        day_before_earthquake = earthquake_date - \
            datetime.timedelta(days=1)

        csv_content_including_postseismic = ['|'.join(header)]
        csv_content_without_postseismic = ['|'.join(header)]

        stations_without_postseismic = set()

        for station in stations_without_postseismic_reduced:
            try:
                station_data = list(models.Stations.objects.values_list('network_code__network_code', 'station_code', 'lat', 'lon').get(
                    network_code__network_code=station['network_code'], station_code=station['station_code']))
                station_data.append(True)
                station_data.append(True)
                # Compute 'Observation Availability'
                observation_availability = models.Rinex.objects.filter(
                    network_code=station_data[0],
                    station_code=station_data[1],
                    observation_s_time__date=day_before_earthquake
                ).exists() and models.Rinex.objects.filter(
                    network_code=station_data[0],
                    station_code=station_data[1],
                    observation_s_time__date=earthquake_date
                ).exists()

                station_data.append(observation_availability)
                coseismic_displacement = coseismic_displacements_dict.get(
                    (station['network_code'], station['station_code']))
                station_data.append(coseismic_displacement.get(
                    'n', 'N/A') if coseismic_displacement else 'N/A')
                station_data.append(coseismic_displacement.get(
                    'e', 'N/A') if coseismic_displacement else 'N/A')
                station_data.append(coseismic_displacement.get(
                    'u', 'N/A') if coseismic_displacement else 'N/A')
                station_data.append(station['distance'])
                station_data.append(station['azimuth'])
                csv_content_without_postseismic.append(
                    '|'.join([str(item) for item in station_data]))

                stations_without_postseismic.add(
                    (station['network_code'], station['station_code']))
            except models.Stations.DoesNotExist:
                raise exceptions.CustomValidationErrorExceptionHandler(
                    f"Station {station['network_code']}.{station['station_code']} does not exist")

        for station in stations_including_postseismic_reduced:
            try:
                station_data = list(models.Stations.objects.values_list('network_code__network_code', 'station_code', 'lat', 'lon').get(
                    network_code__network_code=station['network_code'], station_code=station['station_code']))
                station_data.append(True)
                # Compute "Coseismic" value
                cosesmic_value = (
                    station_data[0], station_data[1]) in stations_without_postseismic
                station_data.append(cosesmic_value)

                # Compute 'Observation Availability'
                observation_availability = models.Rinex.objects.filter(
                    network_code=station_data[0],
                    station_code=station_data[1],
                    observation_s_time__date=day_before_earthquake
                ).exists() and models.Rinex.objects.filter(
                    network_code=station_data[0],
                    station_code=station_data[1],
                    observation_s_time__date=earthquake_date
                ).exists()

                station_data.append(observation_availability)
                coseismic_displacement = coseismic_displacements_dict.get(
                    (station['network_code'], station['station_code']))
                station_data.append(coseismic_displacement.get(
                    'n', 'N/A') if coseismic_displacement else 'N/A')
                station_data.append(coseismic_displacement.get(
                    'e', 'N/A') if coseismic_displacement else 'N/A')
                station_data.append(coseismic_displacement.get(
                    'u', 'N/A') if coseismic_displacement else 'N/A')
                station_data.append(station['distance'])
                station_data.append(station['azimuth'])
                csv_content_including_postseismic.append(
                    '|'.join([str(item) for item in station_data]))
            except models.Stations.DoesNotExist:
                raise exceptions.CustomValidationErrorExceptionHandler(
                    f"Station {station['network_code']}.{station['station_code']} does not exist")

            # Convert to CSV strings
        csv_including_postseismic = '\n'.join(
            csv_content_including_postseismic)
        csv_without_postseismic = '\n'.join(csv_content_without_postseismic)

        return csv_including_postseismic, csv_without_postseismic


class StationMetaUtils:
    @staticmethod
    def update_gaps_status_for_all_station_meta_needed():

        records = models.StationMeta.objects.filter(
            has_gaps_update_needed=True).exclude(station__isnull=True)
        previous_time = datetime.datetime.now()

        for record in records:
            StationMetaUtils.update_gaps_status(record)

        logger.info(
            f' \'has_gaps\' status updated. Total stations updated: {records.count()} - Time taken: {(datetime.datetime.now() - previous_time).total_seconds()}')

        caches['default'].delete('update_gaps_status_lock')

    @staticmethod
    def update_gaps_status(station_meta_record):

        models.StationMetaGaps.objects.filter(
            station_meta=station_meta_record).delete()

        station_gaps = StationMetaUtils.get_station_gaps(station_meta_record)

        for gap in station_gaps:
            gap.save()

        if len(station_gaps) > 0:
            station_meta_record.has_gaps = True
        else:
            station_meta_record.has_gaps = False

        station_meta_record.has_gaps_update_needed = False
        station_meta_record.has_gaps_last_update_datetime = django.utils.timezone.now()

        station_meta_record.save()

    @staticmethod
    def get_station_gaps(station_meta):
        """
        This function checks if there rinex data that falls outside the station info window and returns info about the gaps
        """

        def dictfetchall(cursor):
            "Return all rows from a cursor as a dict"
            columns = [col[0] for col in cursor.description]
            return [
                dict(zip(columns, row))
                for row in cursor.fetchall()
            ]

        def get_rinex_count(station_object, edate, sdate):

            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT count(*) as rcount FROM rinex_proc
                            WHERE "NetworkCode" = %s AND "StationCode" = %s AND
                            "ObservationETime" > %s AND "ObservationSTime" < %s AND
                            "Completion" >= 0.5""", [station_object.network_code.network_code, station_object.station_code, edate, sdate])
                rows = dictfetchall(cursor)

                return rows[0]["rcount"]

        def get_rinex_count_before_date(station_object, date):

            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT count(*) as rcount FROM rinex_proc
                            WHERE "NetworkCode" = %s AND "StationCode" = %s AND
                            "ObservationSTime" < %s AND
                            "Completion" >= 0.5""", [station_object.network_code.network_code, station_object.station_code, date])
                rows = dictfetchall(cursor)

                return rows[0]["rcount"]

        def get_rinex_count_after_date(station_object, date):

            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT count(*) as rcount FROM rinex_proc
                            WHERE "NetworkCode"  = %s AND "StationCode" = %s AND
                            "ObservationETime" > %s AND
                            "Completion" >= 0.5""", [station_object.network_code.network_code, station_object.station_code, date])
                rows = dictfetchall(cursor)

                return rows[0]["rcount"]

        def get_first_and_last_rinex(station_object):

            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT min("ObservationSTime") as first_obs, max("ObservationETime") as last_obs
                            FROM rinex_proc WHERE "NetworkCode" = %s AND "StationCode" = %s
                            AND "Completion" >= 0.5""", [station_object.network_code.network_code, station_object.station_code])
                rows = dictfetchall(cursor)

                return rows[0]

        def has_gaps_between_stationinfo_records(station_object, station_info_records, station_meta):

            gaps_found = []

            if station_info_records.count() > 1:
                # convert station_info_records to a list
                station_info_records = list(station_info_records)

                # get gaps between stninfo records
                for erecord, srecord in zip(station_info_records[0:-1], station_info_records[1:]):

                    sdate = srecord.date_start
                    edate = erecord.date_end

                    if sdate is None or edate is None:
                        logger.error(
                            f"Station info record has None value for date_start or date_end. Station: {station_object.network_code.network_code}.{station_object.station_code}")
                        continue

                    # if the delta between previous and current session exceeds one second, check if any rinex falls
                    # in that gap
                    if (sdate - edate).total_seconds() > 1:

                        rinex_count = get_rinex_count(
                            station_object, edate, sdate)

                        if rinex_count != 0:
                            gaps_found.append(models.StationMetaGaps.objects.create(station_meta=station_meta, rinex_count=rinex_count, record_start_date_start=srecord.date_start,
                                              record_start_date_end=srecord.date_end, record_end_date_start=erecord.date_start, record_end_date_end=erecord.date_end))

            return gaps_found

        def has_gaps_outside_stationinfo_records(station_object, station_info_records, station_meta):
            """
                There should not be RINEX data outside the station info window
            """
            rnxtbl = get_first_and_last_rinex(station_object)

            gaps_found = []

            if rnxtbl["first_obs"] is not None and station_info_records.count() > 0:

                # to avoid empty stations (no rinex data)
                if station_info_records.first().date_start is not None and rnxtbl["first_obs"] < station_info_records.first().date_start:
                    rinex_count = get_rinex_count_before_date(
                        station_object, station_info_records.first().date_start)
                    gaps_found.append(models.StationMetaGaps.objects.create(station_meta=station_meta, rinex_count=rinex_count,
                                      record_start_date_start=station_info_records.first().date_start, record_start_date_end=station_info_records.first().date_end))

                if station_info_records.last().date_end is not None and rnxtbl["last_obs"] > station_info_records.last().date_end:
                    rinex_count = get_rinex_count_after_date(
                        station_object, station_info_records.last().date_end)
                    gaps_found.append(models.StationMetaGaps.objects.create(station_meta=station_meta, rinex_count=rinex_count,
                                      record_end_date_start=station_info_records.last().date_start, record_end_date_end=station_info_records.last().date_end))

            return gaps_found

        station_object = station_meta.station

        station_info_records = models.Stationinfo.objects.filter(
            network_code=station_object.network_code.network_code, station_code=station_object.station_code)

        # check if station_object has the required fields
        if not hasattr(station_object, 'network_code') or not hasattr(station_object, 'station_code'):
            return []

        gaps_found = []

        gaps_found.extend(has_gaps_between_stationinfo_records(
            station_object, station_info_records, station_meta))
        gaps_found.extend(has_gaps_outside_stationinfo_records(
            station_object, station_info_records, station_meta))

        return gaps_found


class EndpointsClusterUtils:
    def group_clusters_by_resource(clusters):
        """
        Group clusters by resource
        """

        clusters_by_resource = {}

        for cluster in clusters:
            resource = cluster['resource']['name']
            if resource not in clusters_by_resource:
                clusters_by_resource[resource] = []
            clusters_by_resource[resource].append(cluster)

        return clusters_by_resource


class UploadMultipleFilesUtils:

    @staticmethod
    def change_file_user_group(file_object, user_group):
        # Get the path of the uploaded file
        file_path = file_object.path

        # Specify the target group name (e.g., 'developers')
        target_group_name = user_group

        # Get the group ID using the group name
        try:
            target_group = grp.getgrnam(target_group_name)
            group_id = target_group.gr_gid

            # Change the group of the file
            # -1 means do not change the user owner
            os.chown(file_path, -1, group_id)
        except KeyError:
            # If running in docker container, it throws an error but in host machine the user group is changed
            pass
        finally:
            # Change the file permissions to give all permissions to the group
            os.chmod(file_path, 0o770)

    @staticmethod
    def upload_multiple_files(view, request, main_object_type):
        if not isinstance(main_object_type, str) or main_object_type not in ('visit', 'station'):
            raise exceptions.CustomServerErrorExceptionHandler(
                "main_object_type must be 'visit' or 'station'")

        files = request.FILES.getlist('file')
        main_objects = request.POST.getlist(main_object_type)
        description = request.POST.getlist('description')

        if len(files) != len(main_objects) or len(files) != len(description):
            raise exceptions.CustomValidationErrorExceptionHandler(
                f"No description or {main_object_type} or filename provided for at least one file")

        try:
            created_files = []
            created_file_instances = set()
            created_file_index = 0
            file_parts_dict = {}

            for file, main_object, description in zip(files, main_objects, description):
                # check if file ends with regex .part followed by a number
                if not bool(re.search(r'part\d+$', file.name)):
                    raise exceptions.CustomValidationErrorExceptionHandler(
                        f"File {file.name} does not end with 'part' and a number")

                # check that file name has just one .part string on it
                if len(file.name.split('.part')) > 2:
                    raise exceptions.CustomValidationErrorExceptionHandler(
                        f"File {file.name} has more than one '.part' string on it")

                file_name = file.name.rsplit('.part', 1)[0]
                if file_name not in file_parts_dict:
                    file_parts_dict[file_name] = []
                file_parts_dict[file_name].append(
                    (file, main_object, description))

            for file_name, parts in file_parts_dict.items():
                parts.sort(key=lambda x: int(
                    x[0].name.rsplit('.part', 1)[1]))
                joined_file_content = b''.join(
                    [part[0].read() for part in parts])

                # Decompress the file using gzip
                try:
                    with gzip.GzipFile(fileobj=BytesIO(joined_file_content), mode='rb') as f:
                        decompressed_data = f.read()
                except Exception as e:
                    raise exceptions.CustomValidationErrorExceptionHandler(
                        f"Failed to decompress file: {str(e)}")

                main_object = parts[0][1]
                description = parts[0][2]
                data = {'file': ContentFile(decompressed_data, name=file_name), main_object_type: main_object,
                        'description': description, 'filename': file_name}

                serializer = view.get_serializer(data=data)
                serializer.is_valid(raise_exception=True)
                created_file_instance = serializer.save()
                created_file_instances.add(created_file_instance)

                created_files.append({created_file_index: serializer.data})
                created_file_index += 1

        except Exception as e:
            for created_file_instance in created_file_instances:
                created_file_instance.delete()

            return Response({"error_message": {file_name: e.detail if hasattr(e, 'detail') else str(e)}}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({"created_files": [created_file.filename for created_file in created_file_instances]}, status=status.HTTP_201_CREATED)

    def upload_multiple_images(view, request, main_object_type):

        if not isinstance(main_object_type, str) or main_object_type not in ('visit', 'station'):
            raise exceptions.CustomServerErrorExceptionHandler(
                "main_object_type must be 'visit' or 'station'")

        images = request.FILES.getlist('image')
        main_objects = request.POST.getlist(main_object_type)
        description = request.POST.getlist('description')
        names = request.POST.getlist('name')

        if len(images) != len(main_objects) or len(images) != len(description) or len(names) != len(images):
            raise exceptions.CustomValidationErrorExceptionHandler(
                f"No description or {main_object_type} or name provided for at least one image")

        try:
            created_images = []
            created_image_instances = set()
            current_image_index = 0
            image_parts_dict = {}

            for image, main_object, description, name in zip(images, main_objects, description, names):
                # check if image ends with regex .part followed by a number
                if not bool(re.search(r'part\d+$', image.name)):
                    image_name = image.name
                    raise exceptions.CustomValidationErrorExceptionHandler(
                        f"Image {image.name} does not end with 'part' and a number")

                # check that image name has just one .part string on it
                if len(image.name.split('.part')) > 2:
                    image_name = image.name
                    raise exceptions.CustomValidationErrorExceptionHandler(
                        f"Image {image.name} has more than one '.part' string on it")

                image_name = image.name.rsplit('.part', 1)[0]
                if image_name not in image_parts_dict:
                    image_parts_dict[image_name] = []
                image_parts_dict[image_name].append(
                    (image, main_object, description, name))

            for image_name, parts in image_parts_dict.items():
                parts.sort(key=lambda x: int(
                    x[0].name.rsplit('.part', 1)[1]))
                joined_image_content = b''.join(
                    [part[0].read() for part in parts])

                # Decompress the image using gzip
                try:
                    with gzip.GzipFile(fileobj=BytesIO(joined_image_content), mode='rb') as f:
                        decompressed_data = f.read()
                except Exception as e:
                    raise exceptions.CustomValidationErrorExceptionHandler(
                        f"Failed to decompress image: {str(e)}")

                main_object = parts[0][1]
                description = parts[0][2]
                name = parts[0][3]
                data = {'image': ContentFile(decompressed_data, name=image_name), main_object_type: main_object,
                        'description': description, 'name': name}

                serializer = view.get_serializer(data=data)
                serializer.is_valid(raise_exception=True)
                created_image_instance = serializer.save()
                created_image_instances.add(created_image_instance)

                created_images.append({current_image_index: serializer.data})
                current_image_index += 1

        except Exception as e:
            for created_image_instance in created_image_instances:
                created_image_instance.delete()

            return Response({"error_message": {image_name: e.detail if hasattr(e, 'detail') else str(e)}}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({"created_images": [created_image.name for created_image in created_image_instances]}, status=status.HTTP_201_CREATED)


class StationInfoUtils:
    @staticmethod
    def get_same_station_records(serializer, get_queryset, get_object=None):

        if get_object != None:
            network_code = get_object().network_code
            station_code = get_object().station_code
        else:
            network_code = serializer.validated_data['network_code']
            station_code = serializer.validated_data['station_code']

        return get_queryset().filter(network_code=network_code, station_code=station_code)

    @staticmethod
    def get_records_that_overlap(serializer, get_queryset, get_object=None):
        # check if the incoming record is between any existing record
        records_that_overlap = []

        q_end = None

        q_start = serializer.validated_data['date_start']

        if 'date_end' in serializer.validated_data:
            q_end = serializer.validated_data['date_end']

        for record in StationInfoUtils.get_same_station_records(serializer, get_queryset, get_object):
            r_start = record.date_start

            r_end = record.date_end

            earliest_end = min(datetime.datetime(9999, 12, 31) if q_end == None else q_end, datetime.datetime(
                9999, 12, 31) if r_end == None else r_end)
            latest_start = max(q_start, r_start)

            if (earliest_end - latest_start).total_seconds() > 0:
                records_that_overlap.append(record)

        return records_that_overlap

    @staticmethod
    def return_stninfo(serializer=None, record=None):
        """
        return a station information string to write to a file (without header
        :return: a string in station information format
        """
        if serializer != None:
            StationInfoUtils.to_dharp(serializer=serializer)
            values = serializer.validated_data.values()
        elif record != None:
            StationInfoUtils.to_dharp(record=record)
            values = record.values()

        return '\n'.join([str(value) for value in values])

    @staticmethod
    def to_dharp(serializer=None, record=None):
        """
        function to convert the current height code to DHARP
        :return: DHARP height
        """
        if serializer != None:
            StationInfoUtils.to_dharp_from_serializer(serializer)
        elif record != None:
            StationInfoUtils.to_dharp_from_record(record)
        else:
            raise exceptions.CustomValidationErrorExceptionHandler(
                'Serializer or record must be provided to convert height code to DHARP.')

    @staticmethod
    def to_dharp_from_serializer(serializer):

        if serializer.validated_data['height_code'] != 'DHARP':
            try:
                htc = models.GamitHtc.objects.get(
                    antenna_code=serializer.validated_data['antenna_code'], height_code=serializer.validated_data['height_code'])

            except models.GamitHtc.DoesNotExist:

                raise exceptions.CustomValidationErrorExceptionHandler('%s.%s: %s -> Could not translate height code %s to DHARP. '
                                                                       'Check the height codes table.'
                                                                       % (serializer.validated_data['network_code'],
                                                                          serializer.validated_data['station_code'],
                                                                          serializer.validated_data['antenna_code'],
                                                                          serializer.validated_data['height_code']))
            else:

                if 'antenna_height' in serializer.validated_data and serializer.validated_data['antenna_height'] is not None:
                    serializer.validated_data['antenna_height'] = numpy.sqrt(numpy.square(float(serializer.validated_data['antenna_height'])) -
                                                                             numpy.square(float(htc.h_offset))) - float(htc.v_offset)
                if 'comments' in serializer.validated_data and isinstance(serializer.validated_data['comments'], str):
                    serializer.validated_data['comments'] = serializer.validated_data['comments'] + '\nChanged from %s to DHARP by Django API.\n' \
                        % serializer.validated_data['height_code']
                else:
                    serializer.validated_data['comments'] = 'Changed from %s to DHARP by Django API.\n' % serializer.validated_data['height_code']

                serializer.validated_data['height_code'] = 'DHARP'

    @staticmethod
    def to_dharp_from_record(record):

        if record['height_code'] != 'DHARP':
            try:
                htc = models.GamitHtc.objects.get(
                    antenna_code=record['antenna_code'], height_code=record['height_code'])

            except models.GamitHtc.DoesNotExist:

                raise exceptions.CustomValidationErrorExceptionHandler('%s.%s: %s -> Could not translate height code %s to DHARP. '
                                                                       'Check the height codes table.'
                                                                       % (record['network_code'],
                                                                          record['station_code'],
                                                                          record['antenna_code'],
                                                                          record['height_code']))
            else:

                record['antenna_height'] = numpy.sqrt(numpy.square(float(record['antenna_height'])) -
                                                      numpy.square(float(htc.h_offset))) - float(htc.v_offset)
                if record['comments'] is not None:
                    record['comments'] = record['comments'] + '\nChanged from %s to DHARP by Django API.\n' \
                        % record['height_code']
                else:
                    record['comments'] = 'Changed from %s to DHARP by Django API.\n' % record['height_code']

                record['height_code'] = 'DHARP'

    def record_to_str(record):
        return str(StationInfoUtils.get_record_values(record=record))

    def get_record_values(get_object=None, record=None):
        if record != None:
            values = {field.name: getattr(record, field.name)
                      for field in models.Stationinfo._meta.get_fields()}

        elif get_object != None:
            values = {field.name: getattr(get_object(
            ), field.name) for field in models.Stationinfo._meta.get_fields()}

        return values


class RinexUtils:
    @staticmethod
    def get_next_station_info(rinex):

        related_station_info = models.Stationinfo.objects.filter(
            network_code=rinex.network_code,
            station_code=rinex.station_code
        ).order_by('date_start')

        if related_station_info.count() == 0:
            raise exceptions.CustomValidationErrorExceptionHandler(
                'No station info found for %s.%s' % (rinex.network_code, rinex.station_code))

        station_info_before_rinex, station_info_containing_rinex, station_info_after_rinex = RinexUtils._clasify_station_info_from_rinex(
            rinex, related_station_info)

        if len(station_info_after_rinex) > 0:
            return station_info_after_rinex[0]
        else:
            return None

    @staticmethod
    def get_previous_station_info(rinex):

        related_station_info = models.Stationinfo.objects.filter(
            network_code=rinex.network_code,
            station_code=rinex.station_code
        ).order_by('date_start')

        if related_station_info.count() == 0:
            raise exceptions.CustomValidationErrorExceptionHandler(
                'No station info found for %s.%s' % (rinex.network_code, rinex.station_code))

        station_info_before_rinex, station_info_containing_rinex, station_info_after_rinex = RinexUtils._clasify_station_info_from_rinex(
            rinex, related_station_info)

        if len(station_info_before_rinex) > 0:
            return station_info_before_rinex[-1]
        else:
            return None

    @staticmethod
    def get_rinex_with_status(rinex_list, filters):
        if len(rinex_list) > 0:
            station_info_from_station = list(RinexUtils._get_station_info_from_station(
                rinex_list[0].network_code, rinex_list[0].station_code))

            rinex_with_related_station_info = []

            for rinex in rinex_list:
                station_info_before_rinex, station_info_containing_rinex, station_info_after_rinex = RinexUtils._clasify_station_info_from_rinex(
                    rinex, station_info_from_station)

                rinex.has_station_info = len(station_info_containing_rinex) > 0

                rinex.has_multiple_station_info_gap = RinexUtils._get_has_multiple_station_info_gap(
                    rinex, station_info_from_station)

                rinex.metadata_mismatch = RinexUtils._check_metadata_mismatch(
                    rinex, station_info_containing_rinex)

                rinex.gap_type = RinexUtils._get_gap_type(
                    rinex, station_info_before_rinex, station_info_containing_rinex, station_info_after_rinex)

                rinex_with_related_station_info.append(
                    (rinex, station_info_containing_rinex))

            rinex_list = RinexUtils._group_by_same_date_range(
                rinex_with_related_station_info)

            rinex_list = RinexUtils._group_by_date_range_distance(rinex_list)

            rinex_list = RinexUtils._convert_to_correct_format(rinex_list)

            rinex_list = RinexUtils._filter_rinex(rinex_list, filters)

            return rinex_list
        else:
            return []

    @staticmethod
    def _is_filtered(rinex, filters):
        datetime_string_format = "%Y-%m-%d %H:%M"

        if filters["observation_doy"] != None and str(filters["observation_doy"]) != str(int(rinex["observation_doy"])):
            return True
        elif filters["observation_f_year"] != None and str(filters["observation_f_year"]) != str(round(float(rinex["observation_f_year"]), 3)):
            return True
        elif filters["observation_s_time_since"] != None and datetime.datetime.strptime(filters["observation_s_time_since"], datetime_string_format).replace(second=0) > rinex["observation_s_time"].replace(second=0):
            return True
        elif filters["observation_s_time_until"] != None and datetime.datetime.strptime(filters["observation_s_time_until"], datetime_string_format).replace(second=0) < rinex["observation_s_time"].replace(second=0):
            return True
        elif filters["observation_e_time_since"] != None and datetime.datetime.strptime(filters["observation_e_time_since"], datetime_string_format).replace(second=0) > rinex["observation_e_time"].replace(second=0):
            return True
        elif filters["observation_e_time_until"] != None and datetime.datetime.strptime(filters["observation_e_time_until"], datetime_string_format).replace(second=0) < rinex["observation_e_time"].replace(second=0):
            return True
        elif filters["observation_year"] != None and str(filters["observation_year"]) != str(int(rinex["observation_year"])):
            return True
        elif filters["antenna_dome"] != None and str(filters["antenna_dome"]) not in str(rinex["antenna_dome"]):
            return True
        elif filters["antenna_offset"] != None and str(filters["antenna_offset"]) not in str(rinex["antenna_offset"]):
            return True
        elif filters["antenna_serial"] != None and str(filters["antenna_serial"]) not in str(rinex["antenna_serial"]):
            return True
        elif filters["antenna_type"] != None and str(filters["antenna_type"]) not in str(rinex["antenna_type"]):
            return True
        elif filters["receiver_fw"] != None and str(filters["receiver_fw"]) not in str(rinex["receiver_fw"]):
            return True
        elif filters["receiver_serial"] != None and str(filters["receiver_serial"]) not in str(rinex["receiver_serial"]):
            return True
        elif filters["receiver_type"] != None and str(filters["receiver_type"]) not in str(rinex["receiver_type"]):
            return True
        elif filters["completion_operator"] != None and filters["completion"] != None and RinexUtils._filter_rinex_by_completion(rinex, filters["completion_operator"], filters["completion"]):
            return True
        elif filters["interval"] != None and str(filters["interval"]) not in str(rinex["interval"]):
            return True
        else:
            return False

    @staticmethod
    def _filter_rinex_by_completion(rinex, completion_operator, completion):
        if completion_operator.upper().strip() == "GREATER_THAN":
            return float(rinex["completion"]) <= float(completion)
        elif completion_operator.upper().strip() == "LESS_THAN":
            return float(rinex["completion"]) >= float(completion)
        elif completion_operator.upper().strip() == "EQUAL":
            return float(rinex["completion"]) != float(completion)
        else:
            return False

    @staticmethod
    def _filter_rinex(rinex_list, filters):
        for first_groups in rinex_list:
            for second_groups in first_groups["rinex"]:
                for rinex in second_groups["rinex"]:
                    rinex["filtered"] = RinexUtils._is_filtered(rinex, filters)

        return rinex_list

    @staticmethod
    def _get_station_info_from_station(network_code, station_code):
        return models.Stationinfo.objects.filter(network_code=network_code, station_code=station_code).order_by('date_start')

    @staticmethod
    def _get_has_multiple_station_info_gap(rinex, station_info_list):
        station_info_containing_start_date = None
        station_info_containing_end_date = None

        for station_info in station_info_list:
            station_info_date_end = station_info.date_end if station_info.date_end is not None else datetime.datetime(
                9999, 12, 31)

            if station_info.date_start <= rinex.observation_s_time and station_info_date_end >= rinex.observation_s_time:
                station_info_containing_start_date = station_info
            if station_info.date_start <= rinex.observation_e_time and station_info_date_end >= rinex.observation_e_time:
                station_info_containing_end_date = station_info

        return station_info_containing_start_date is not None and station_info_containing_end_date is not None and station_info_containing_start_date != station_info_containing_end_date

    @staticmethod
    def _convert_rinex_to_dict(rinex):
        has_station_info = rinex.has_station_info
        has_multiple_station_info_gap = rinex.has_multiple_station_info_gap
        metadata_mismatch = rinex.metadata_mismatch
        gap_type = rinex.gap_type

        rinex = model_to_dict(rinex)

        rinex['has_station_info'] = has_station_info
        rinex['has_multiple_station_info_gap'] = has_multiple_station_info_gap
        rinex['metadata_mismatch'] = metadata_mismatch
        rinex['gap_type'] = gap_type

        return rinex

    @staticmethod
    def _convert_stationinfo_to_dict(stationinfo):

        stationinfo = {"api_id": stationinfo.api_id,
                       "date_start": stationinfo.date_start, "date_end": stationinfo.date_end}

        return stationinfo

    @staticmethod
    def _convert_to_correct_format(rinex_list):
        # Converts to a list of dict and sort all the stationinfo lists

        list_dict = []

        for stationinfo_super_set, rinex_with_related_stationinfo_list in rinex_list:

            stationinfo_super_set_list = list(stationinfo_super_set)
            stationinfo_super_set_list.sort(key=lambda x: x.date_start)

            correct_format_super_group = {
                'related_station_info': [RinexUtils._convert_stationinfo_to_dict(stationinfo_super_set_list_element) for stationinfo_super_set_list_element in stationinfo_super_set_list],
                'rinex': [
                    {
                        'rinex': [RinexUtils._convert_rinex_to_dict(rinex_element) for rinex_element in rinex],
                        'related_station_info': [RinexUtils._convert_stationinfo_to_dict(related_station_info_element) for related_station_info_element in sorted(list(stationinfo_from_rinex), key=lambda x: x.date_start)]
                    }
                    for rinex, stationinfo_from_rinex in rinex_with_related_stationinfo_list
                ]
            }
            list_dict.append(correct_format_super_group)

        return list_dict

    @staticmethod
    def _group_by_same_date_range(rinex_list):

        rinex_list.sort(key=lambda x: x[0].observation_s_time)

        rinex_groups = []

        for rinex, station_info in rinex_list:
            if len(rinex_groups) == 0:
                rinex_groups.append([(rinex, station_info)])
            else:
                if rinex_groups[-1][0][0].observation_s_time == rinex.observation_s_time and rinex_groups[-1][0][0].observation_e_time == rinex.observation_e_time:
                    rinex_groups[-1].append((rinex, station_info))
                else:
                    rinex_groups.append([(rinex, station_info)])

        # group station info from same rinex group and remove duplicates

        rinex_groups_with_station_info = []

        for rinex_group in rinex_groups:
            station_info_set = set()
            rinexs = []

            for rinex, station_info in rinex_group:
                rinexs.append(rinex)
                station_info_set.update(station_info)

            rinex_groups_with_station_info.append((rinexs, station_info_set))

        return rinex_groups_with_station_info

    @staticmethod
    def _group_by_date_range_distance(rinex_list):
        # iterate over rinex_list with format [([rinex1_1, rinex1_2], set(stationinfo1, stationinfo2)), ([rinex2_1, rinex2_2], set(stationinfo1, stationinfo2))]
        # take the first rinex of each group compare them like if rinex2_1.observation_s_time - rinex1_1.observation_e_time) < settings.RINEX_STATUS_DATE_SPAN_SECONDS
        # if the condition is met, add all rinex and stationinfo from both groups into a new group.
        # continue until a rinex doesn't fulfill the condition. Then, that rinex will be the first one of the next group.

        rinex_list = list(rinex_list)

        rinex_groups = []

        for rinex_group, station_info_set in rinex_list:
            if len(rinex_groups) == 0:
                rinex_groups.append(
                    (station_info_set.copy(), [(rinex_group, station_info_set.copy())]))
            else:
                if (rinex_group[0].observation_s_time - rinex_groups[-1][1][-1][0][0].observation_e_time).total_seconds() < float(settings.RINEX_STATUS_DATE_SPAN_SECONDS):

                    rinex_groups[-1][1].append((rinex_group,
                                               station_info_set.copy()))
                    rinex_groups[-1][0].update(station_info_set)

                else:
                    rinex_groups.append(
                        (station_info_set.copy(), [(rinex_group, station_info_set)]))

        return rinex_groups

    @staticmethod
    def _get_gap_type(rinex, station_info_before_rinex, station_info_containing_rinex, station_info_after_rinex):
        if not hasattr(rinex, 'has_multiple_station_info_gap'):
            raise exceptions.CustomServerErrorExceptionHandler(
                'Rinex object must have the attribute has_multiple_station_info_gap')

        if rinex.has_multiple_station_info_gap:
            return None
        elif len(station_info_before_rinex) == 0 and len(station_info_containing_rinex) == 0 and len(station_info_after_rinex) == 0:
            return "NO STATION INFO"
        elif len(station_info_containing_rinex) > 0:
            return None
        else:
            if len(station_info_before_rinex) == 0:
                return "BEFORE FIRST STATIONINFO"
            elif len(station_info_after_rinex) == 0:
                return "AFTER LAST STATIONINFO"
            else:
                return "BETWEEN TWO STATIONINFO"

    @staticmethod
    def _check_metadata_mismatch(rinex, station_info_containing_rinex):
        if len(station_info_containing_rinex) > 0:
            mismatches = []

            if rinex.receiver_type != station_info_containing_rinex[0].receiver_code:
                mismatches.append('receiver_type')

            if rinex.receiver_serial != station_info_containing_rinex[0].receiver_serial:
                mismatches.append('receiver_serial')

            if rinex.receiver_fw != station_info_containing_rinex[0].receiver_firmware:
                mismatches.append('receiver_fw')

            if rinex.antenna_type != station_info_containing_rinex[0].antenna_code:
                mismatches.append('antenna_type')

            if rinex.antenna_serial != station_info_containing_rinex[0].antenna_serial:
                mismatches.append('antenna_serial')

            if rinex.antenna_dome != station_info_containing_rinex[0].radome_code:
                mismatches.append('antenna_dome')

            if rinex.antenna_offset != station_info_containing_rinex[0].antenna_height:
                mismatches.append('antenna_offset')

            return mismatches
        else:
            return []

    @staticmethod
    def _clasify_station_info_from_rinex(rinex, station_info_from_station):
        station_info_before_rinex = []
        station_info_containing_rinex = []
        station_info_after_rinex = []

        for station_info in station_info_from_station:

            station_info_date_end = station_info.date_end if station_info.date_end is not None else datetime.datetime(
                9999, 12, 31)

            if station_info.date_start < rinex.observation_s_time and station_info_date_end < rinex.observation_s_time:
                station_info_before_rinex.append(station_info)
            elif rinex.observation_s_time >= station_info.date_start and rinex.observation_s_time <= station_info_date_end and rinex.observation_e_time > station_info_date_end:
                station_info_before_rinex.append(station_info)
            elif rinex.observation_s_time < station_info.date_start and rinex.observation_e_time >= station_info.date_start and rinex.observation_e_time <= station_info_date_end:
                station_info_after_rinex.append(station_info)
            elif station_info.date_start <= rinex.observation_s_time and station_info_date_end >= rinex.observation_e_time:
                station_info_containing_rinex.append(station_info)
            elif station_info.date_start > rinex.observation_e_time:
                station_info_after_rinex.append(station_info)

        return station_info_before_rinex, station_info_containing_rinex, station_info_after_rinex

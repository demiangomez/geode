import { User } from "./index.d";
declare module "@heroicons/*";

export interface GetParams {
    with_people?: boolean;
    without_photo?: boolean;
    without_actual_files?: boolean;
    network_code?: string;
    thumbnail?: boolean;
    country_code?: string;
    station_code?: string;
    antenna_code?: string;
    station_api_id?: string;
    visit_api_id?: string;
    api_id?: string;
    only_metadata?: boolean;
    doy?: string;
    event_type?: string;
    event_date_since?: string;
    event_date_until?: string;
    module?: string;
    node?: string;
    stack?: string;
    year?: string;
    description?: string;
    role_type?: "FRONT" | "API";
    solution?: string;
    date_start?: string;
    date_end?: string;
    residuals?: boolean;
    missing_data?: boolean;
    plot_outliers?: boolean;
    plot_auto_jumps?: boolean;
    no_model?: boolean;
    remove_jumps?: boolean;
    remove_polynomial?: boolean;
    observation_doy?: string | number;
    observation_f_year?: string | number;
    observation_s_time_since?: string;
    observation_s_time_until?: string;
    observation_e_time_since?: string;
    observation_e_time_until?: string;
    observation_year?: string | number;
    antenna_dome?: string;
    antenna_offset?: string | number;
    antenna_serial?: string;
    antenna_type?: string;
    receiver_fw?: string;
    receiver_serial?: string;
    receiver_type?: string;
    completion_operator?: string;
    completion?: string | number;
    interval?: string | number;
    offset?: number;
    limit?: number;
    monument_id?: number;
    campaign?: number;
}

export interface MyMapContainerProps {
    center: LatLngExpression;
    zoom: number;
    scrollWheelZoom: boolean;
    style?: React.CSSProperties;
    minZoom?: number;
}

export interface TokenPayload {
    token_type: string;
    user_id: number;
    role_id: number;
    role_name: string;
    username: string;
    jti: string;
    iat: number;
    exp: number;
}

export interface DropdownState {
    dropdown: boolean;
    type: string | undefined;
}

export interface EarthquakeData {
    api_id: number;
    date: string;
    lat: number;
    lon: number;
    depth: number;
    mag: number;
    strike1: number;
    dip1: number;
    rake1: number;
    strike2: number;
    dip2: number;
    rake2: number;
    id: string;
    location: string;
}

export interface EarthQuakeParams {
    date_start?: string;
    date_end?: string;
    max_magnitude?: number | undefined;
    min_magnitude?: number | undefined;
    id?: string | undefined;
    max_depth?: number | undefined;
    min_depth?: number | undefined;
}

export interface EarthQuakeFormState {
    date_start: string | undefined;
    date_end: string | undefined;
    max_magnitude: string;
    min_magnitude: string;
    id: string;
    max_depth: string;
    min_depth: string;
    min_latitude: string;
    max_latitude: string;
    min_longitude: string;
    max_longitude: string;
    polygon_coordinates: [number[]];
}

export interface ErrorResponse {
    msg: string;
    response: Errors;
    status: string;
    statusCode: number;
}

export interface FilesErrorResponse {
    msg: string;
    response: FileErrors;
    status: string;
    statusCode: number;
}

export interface PatchDescriptionImageResponse {
    actual_image: string;
    description: string;
    id: number;
    name: string;
    station: number;
    statusCode: number;
}

export interface PatchDescriptionVisitImageResponse {
    actual_image: string;
    description: string;
    id: number;
    name: string;
    statusCode: number;
    visit: number;
}

export interface RinexAddFile {
    msg: string;
    response: RinexFileResponse;
    status: string;
    statusCode: number;
}

export interface ExtendedErrors extends Errors {
    statusCode: number;
}

export interface Errors {
    errors: [{ code: string; detail: string; attr: string }];
    type: string;
}

export interface FileErrors {
    error_message: [{ [key: string]: string[] }];
}

export interface KmzFile {
    kmz: string;
    statusCode: number;
}

export interface StationServiceData {
    count: number;
    total_count: number;
    data: StationData[];
    statusCode: number;
}

export interface StationsAffectedServiceData {
    affected_stations_including_postseismic: StationAffectedInfo[];
    affected_stations_without_postseismic: StationAffectedInfo[];
    csv_including_postseismic: string;
    csv_without_postseismic: string;
    kml_including_postseismic: string;
    kml_without_postseismic: string;
    coseismic_displacements: CoseismicDisplacement[];
}

export interface CoseismicDisplacement {
    NetworkCode: string;
    StationCode: string;
    n: number;
    e: number;
    u: number;
}

export interface StationAffectedInfo {
    network_code: string;
    station_code: string;
}

export interface FilterState {
    typeOption: string[];
    statusOption: string[];
}

export interface ExtendedStationInfoData extends StationInfoData {
    statusCode: number;
}

export interface StationMetadataServiceData {
    battery_description: string;
    communications_description: string;
    has_battery: boolean;
    has_communications: boolean;
    has_gaps: boolean;
    has_gaps_last_update_datetime: string;
    has_gaps_update_needed: boolean;
    id: string;
    comments: string;
    monument_type: string;
    navigation_actual_file: string | null;
    navigation_filename: string;
    observations_actual_file: string | null;
    observations_filename: string;
    remote_access_link: string;
    station: string;
    station_type: string | null;
    status: string;
    station_type_name: string | null;
    station_status_name?: string | null;
    statusCode: string;
    station_name?: string;
    rinex_count?: number;
    distinct_visit_years: string[];
}

export interface StationInfoServiceData {
    count: number;
    data: StationInfoData[];
    total_count: number;
    statusCode: number;
}

export interface StationStatusServiceData {
    count: number;
    total_count: number;
    data: StationStatusData[];
    statusCode: number;
}

export interface StationEventsData {
    count: number;
    total_count: number;
    data: StationEvents[];
    statusCode: number;
}

export interface StationFilesServiceData {
    count: number;
    total_count: number;
    data: StationFilesData[];
    statusCode: number;
}

export interface StationVisitsServiceData {
    count: number;
    total_count: number;
    data: StationVisitsData[];
    statusCode: number;
}

export interface StationCampaignsServiceData {
    count: number;
    total_count: number;
    data: StationCampaignsData[];
    statusCode: number;
}

export interface StationVisitsFilesServiceData {
    count: number;
    total_count: number;
    data: StationVisitsFilesData[];
    statusCode: number;
}

export interface MonumentTypesServiceData {
    count: number;
    total_count: number;
    data: MonumentTypes[];
    statusCode: number;
}

export interface ReceiversServiceData {
    count: number;
    total_count: number;
    data: ReceiversData[];
    statusCode: number;
}

export interface AntennaServiceData {
    count: number;
    total_count: number;
    data: AntennaData[];
    statusCode: number;
}

export interface GamitHTCServiceData {
    count: number;
    data: GamitHTCData[];
    total_count: number;
}

export interface NetworkServiceData {
    count: number;
    total_count: number;
    data: NetworkData[];
    statusCode: number;
}

export interface CountriesServiceData {
    count: number;
    total_count: number;
    data: CountriesData[];
    statusCode: number;
}

export interface RinexServiceData {
    count: number;
    total_count: number;
    data: RinexData[];
    statusCode: number;
}

export interface RinexRelatedStationInfo {
    api_id: number;
    date_end: string;
    date_start: string;
}

interface RinexItem {
    rinex: RinexData[];
    related_station_info: RinexRelatedStationInfo[];
}

interface RinexObject {
    related_station_info: RinexRelatedStationInfo[];
    rinex: RinexItem[];
    groupId?: string;
}

export interface RinexFileResponse {
    inserted_station_info: {
        station_code?: string;
        network_code?: string;
        date_start?: string;
    }[];
    error_message?: {
        [key: string]: string[];
    };
    statusCode: 400 | 201;
}

export interface LoginServiceData {
    refresh: string;
    access: string;
}

export interface UsersServiceData {
    count: number;
    total_count: number;
    data: UsersData[];
}

export interface RolesServiceData {
    count: number;
    total_count: number;
    data: Role[];
}

export interface ClusterServiceData {
    count: number;
    total_count: number;
    data: EndpointCluster[];
}

export interface FrontPagesServiceData {
    count: number;
    total_count: number;
    data: FrontPagesData[];
}

export interface RolePersonStationServiceData {
    count: number;
    total_count: number;
    data: RolePersonStationData[];
    statusCode: number;
}

export interface CampaignsServiceData {
    count: number;
    total_count: number;
    data: CampaignsData[];
    statusCode: number;
}

export interface CampaignsData {
    id: number;
    name: string;
    start_date: string;
    end_date: string;
    default_people: number[];
    statusCode: number;
    default_people_id?: number;
}

export interface ExtendedUsersData extends UsersData {
    statusCode: number;
}

export interface User {
    first_name: string;
    last_name: string;
    username: string;
    password: string;
    role: number | null;
    email?: string;
    phone?: string;
    address?: string;
    photo?: string | null;
    is_active?: boolean | null;
}
//heredo de User
type UsersData = Omit<User, "role"> & {
    id: number | null;
    clustering_distance?: string | null;
    role: { id: number; name: string };
    person?: People | null;
};

export interface Role {
    id: number;
    name: string;
    role_api: boolean;
    is_active: boolean;
    allow_all: boolean;
    endpoints_clusters: number[];
    pages: number[];
    statusCode: number;
}

export interface Cluster {
    id: number;
    name: string;
}

export interface ExtendedMonumentTypes extends MonumentTypes {
    statusCode: number;
}

export interface MonumentTypes {
    id: number;
    name: string;
    photo_file: string | null;
}

export interface ExtendedStationStatus extends StationStatus {
    statusCode: number;
}

export interface StationStatus {
    id: number;
    name: string;
}

export interface StationFilesData {
    id: number;
    station: number;
    filename: string;
    actual_file: string;
    description: string;
    statusCode: number;
}

export interface StationEvents {
    event_id: string | null;
    event_date: string | null;
    event_type: string | null;
    network_code: string | null;
    station_code: string | null;
    year: string | null;
    doy: string | null;
    description: string | null;
    stack: string | null;
    module: string | null;
    node: string | null;
}

export interface VisitFilesData {
    id: number;
    visit: number;
    filename: string;
    description: string;
    statusCode: number;
}

export interface CompletionPlotServiceData {
    completion_plot: string;
    statusCode: number;
}

export interface StationVisitsData {
    [key: string]: string | number;
    campaign: string | null;
    campaign_name?: string | null;
    campaign_people: string | null;
    date: string;
    id: number;
    // api_id added to avoid error on station when redirect visit from campaign
    api_id?: number;
    log_sheet_actual_file: string | null;
    log_sheet_filename: string;
    navigation_actual_file: string | null;
    navigation_filename: string;
    people: string[{ id: number; name: string }];
    station: number;
    station_network_code: string;
    station_station_code: string;
    comments: string;
    observation_file_count: number;
    visit_image_count: number;
    other_file_count: number;
}

export interface StationPostVisitData {
    campaign: string;
    date: string;
    id: number;
    log_sheet_actual_file: string | null;
    log_sheet_filename: string;
    navigation_actual_file: string | null;
    navigation_filename: string;
    people: number[];
    station: number;
    statusCode: number;
}

export interface StationCampaignsData {
    id: number;
    name: string;
    start_date: string;
    end_date: string;
    default_people: number[];
}

export interface StationVisitsFilesData {
    actual_image: string | null;
    actual_file?: string | null;
    filename: string;
    description: string;
    name: string;
    id: number;
    visit: number;
    size?: string;
}

export interface StationImagesData {
    actual_image: string | null;
    filename: string;
    description: string;
    name: string;
    id: number;
    station: number;
}

export interface StationTypeServiceData {
    count: number;
    total_count: number;
    data: StationTypeData[];
    statusCode: number;
}

export interface StationStatusServiceData {
    count: number;
    total_count: number;
    data: StationStatusData[];
    statusCode: number;
}

export interface ColorData {
    id: number;
    color: string;
}

export interface ColorServiceData {
    count: number;
    data: colorData[];
    total_count: number;
    statusCode: number;
}

export interface StationTypeData {
    actual_image: string | File;
    id: number;
    name: string;
    search_icon_on_assets_folder: boolean;
}

export interface StationStatusData {
    id: number;
    color_name: string;
    name: string;
    color: number;
}

export interface StationImagesServiceData {
    count: number;
    total_count: number;
    data: StationPhotosData[];
    statusCode: number;
}

export interface PeopleServiceData {
    count: number;
    total_count: number;
    data: People[];
}

export interface ExtendedPeople extends People {
    statusCode: number;
}

export interface People {
    id: number;
    first_name: string;
    last_name: string;
    email: string;
    phone: string;
    address: string;
    photo_actual_file: string;
    user?: number | string | null;
    institution?: string;
    position?: string;
    user_name: string;
}

export type PeopleSelectedData =
    | undefined
    | [
          number,
          string,
          string,
          string,
          string,
          string,
          number | string,
          string,
          string,
          string,
      ];

export interface EndpointCluster {
    [key: string]: [
        {
            id: number;
            resource: string;
            description: string;
            cluster_type: Cluster;
            endpoints: [number];
        },
    ];
}

export interface FrontPagesData {
    [key: string]: [
        {
            description: string;
            endpoint_clusters: [number];
            id: number;
            url: string;
        },
    ];
}

export interface NetworkData {
    api_id: number;
    network_code: string;
    network_name: string;
}

export interface CountriesData {
    id: number;
    name: string;
    three_digits_code: string;
    two_digits_code: string;
}

export interface RinexData {
    network_code: string;
    station_code: string;
    filtered: boolean;
    observation_year: number;
    observation_month: number;
    observation_day: number;
    observation_doy: number;
    observation_f_year: number;
    observation_s_time: string;
    observation_e_time: string;
    receiver_type: string;
    receiver_serial: string;
    receiver_fw: string;
    antenna_type: string;
    antenna_serial: string;
    antenna_dome: string;
    filename: string;
    interval: number;
    antenna_offset: number;
    completion: number;
    api_id: number;
    has_station_info: boolean;
    has_multiple_station_info_gap: boolean;
    metadata_mismatch: string[];
    gap_type: string | null;
}

export interface ExtendedStationData extends StationData {
    statusCode: number;
}

export interface GapData {
    record_end_date_end: string | null;
    record_end_date_start: string | null;
    record_start_date_end: string | null;
    record_start_date_start: string | null;
    rinex_count: number;
    station_meta: number;
}

export interface StationData {
    api_id?: number;
    visitDetail?: any;
    network_code: string;
    station_code: string;
    station_name: string;
    date_start: number;
    date_end: number;
    auto_x: number;
    auto_y: number;
    auto_z: number;
    harpos_coeff_otl: string;
    has_gaps: boolean;
    has_stationinfo: boolean;
    lat: number;
    lon: number;
    height: number;
    max_dist: number;
    dome: string;
    country_code: string;
    marker: number;
    gaps: GapData[];
    mainParams?: GetParams;
    status: string;
    type: string | null;
}

export interface StationInfoData {
    antenna_code: string;
    antenna_east: string;
    antenna_height: string;
    antenna_north: string;
    antenna_serial: string;
    api_id: number;
    comments: null | string;
    date_end: string;
    date_start: string;
    height_code: string;
    network_code: string;
    radome_code: string;
    receiver_code: string;
    receiver_firmware: string;
    receiver_serial: string;
    receiver_vers: string;
    station_code: string;
}

export interface ReceiversData {
    api_id: number;
    receiver_code: string;
    receiver_description: string | null;
}

export interface AntennaData {
    api_id: number;
    antenna_code: string;
    antenna_description: string | null;
}

export interface GamitHTCData {
    antenna_code: string;
    api_id: number;
    h_offset: number;
    height_code: string;
    v_offset: number;
}

export interface RolePersonStationData {
    id: number;
    role: number;
    person: number;
    station: number;
}

export interface ExtendedRolePersonStationData extends RolePersonStationData {
    statusCode: number;
}

export interface ConfigJumpData {
    Year: number;
    DOY: number;
    action: string;
    fit: boolean;
    type: number;
    type_name: string;
    metadata: string;
    relaxation: number[];
    type: string;
}

export interface JumpType {
    id: number;
    type: string;
}

export interface ConfigPolynomialData {
    DOY: number;
    Year: number;
    terms: number;
}

export interface TimeSeriesParamsData {
    jumps: ConfigJumpData[];
    periodic: any;
    polynomial: ConfigPolynomialData;
}

export interface TimeSeriesParamsServiceData {
    current_config: TimeSeriesParamsData;
    statusCode: number;
}

export interface SourcesServerServiceData {
    count: number;
    data: SourcesServerData[];
    statusCode: number;
    total_count: number;
}

export interface SourcesServerData {
    format: string;
    fqdn: string;
    password: string;
    path: string | null;
    protocol: string;
    server_id: number;
    username: string;
}

export interface SourcesFormatServiceData {
    count: number;
    data: SourcesFormatData[];
    statusCode: number;
    total_count: number;
}

export interface SourcesFormatData {
    api_id: number;
    format: string;
}

export interface SourcesStationsServiceData {
    count: number;
    data: SourcesStationsData[];
    statusCode: number;
    total_count: number;
}

export interface SourcesStationsData {
    api_id: number;
    network_code: string;
    station_code: string;
    try_order: number;
    path: string | null;
    server_id: number;
    format: string;
}

export interface Filter {
    type?: string;
    title: string;
    multiple_titles?: { title: string; label: string }[];
    input: string | boolean;
    multiple_inputs?: string[] | { title: string; label: string }[];
    to?: boolean;
    label: string;
}
export interface TraceData {
    api_id: number;
    network_code: string;
    station_code: string;
    observation_year: number;
    observation_month: number;
    observation_day: number;
    observation_doy: number;
    observation_f_year: number;
    observation_s_time: string;
    observation_e_time: string;
    receiver_type: string;
    receiver_serial: string;
    receiver_fw: string;
    antenna_type: string;
    antenna_serial: string;
    antenna_dome: string;
    antenna_height?: float;
    antenna_code?: string;
    radome_code?: string;
    filename: string;
    interval: number;
    antenna_offset: float;
    completion: number;
    antenna_north?: float;
    antenna_east?: float;
    height_code?: string;
    receiver_code?: string;
    date_start?: string;
    date_end?: string;
}

// Tipo para la respuesta de la API
export interface TraceResponse {
    count: number;
    total_count: number;
    data: TraceData[];
}

// Tipo para los filtros/toggles
export interface RinexFilters {
    showHeight: boolean;
    showHeightCode: boolean;
    showAntennaCode: boolean;
    showAntennaSerial: boolean;
    showAntennaRadome: boolean;
    showReceiverCode: boolean;
    showReceiverType: boolean;
    showObservationStartTime: boolean;
    showObservationEndTime: boolean;
}

export interface StationInfoFilters {
    showHeight: boolean;
    showHeightCode: boolean;
    showAntennaCode: boolean;
    showAntennaSerial: boolean;
    showNorth: boolean;
    showEast: boolean;
    showAntennaRadome: boolean;
    showReceiverCode: boolean;
    showReceiverSerial: boolean;
    showDateStart: boolean;
    showDateEnd: boolean;
}

import { AxiosInstance } from "axios";
import { axiosInstanceUnauth } from "./axiosconfig";
import { EarthQuakeParams, GetParams, StationData, User } from "@types";
import { transformParams, transformParamsForFilter } from "@utils";
/* <----------------------- UN AUTH -----------------------------> */

export async function loginService<T>(
    username: string,
    password: string,
): Promise<T> {
    try {
        const data = { username, password };
        const response = await axiosInstanceUnauth.post(`api/token`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function refreshTokenService<T>(token: string): Promise<T> {
    try {
        const response = await axiosInstanceUnauth.post(`api/token/refresh`, {
            refresh: token,
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

/* <------------------------------------------------------------> */

/*
               SERVICIOS CON AUTENTICACION

 <------------------------- AUTH -------------------------------> */
// SERVER

export async function getServerHealthService<T>(
    api: AxiosInstance,
): Promise<T> {
    try {
        const response = await api.get(`api/health-check`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Users
export async function getUsersService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/users${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function deleteUserService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/users/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getUserPhotoService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/users/${id}/photo`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getUserService<T>(
    api: AxiosInstance,
    id: number,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/users/${id}${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postUserService<T>(
    api: AxiosInstance,
    data: FormData,
): Promise<T> {
    try {
        const response = await api.post(`api/users`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function patchUserService<T>(
    api: AxiosInstance,
    id: number,
    data: FormData | User | { is_active: boolean },
): Promise<T> {
    try {
        const response = await api.patch(`api/users/${id}`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}
// Roles
export async function getRolesService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/roles${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getRoleService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/roles/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

type RoleData = {
    name: string;
    role_api: boolean;
    allow_all: boolean;
    endpoints_clusters: number[] | [];
};

export async function postRoleService<T>(
    api: AxiosInstance,
    data: RoleData,
): Promise<T> {
    try {
        const response = await api.post(`api/roles`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function putRoleService<T>(
    api: AxiosInstance,
    id: number,
    data: RoleData,
): Promise<T> {
    try {
        const response = await api.put(`api/roles/${id}`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Endpoint Clusters

export async function getEndpointClustersService<T>(
    api: AxiosInstance,
    params: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/endpoints-clusters${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Pages
export async function getPagesService<T>(api: AxiosInstance): Promise<T> {
    try {
        const response = await api.get(`api/pages`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getPageService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/pages/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postPageService<T>(
    api: AxiosInstance,
    data: { url: string; description: string },
): Promise<T> {
    try {
        const response = await api.post(`api/pages`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function putPageService<T>(
    api: AxiosInstance,
    id: number,
    data: { url: string; description: string },
): Promise<T> {
    try {
        const response = await api.put(`api/pages/${id}`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delPageService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/pages/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Networks
export async function getNetworksService<T>(api: AxiosInstance): Promise<T> {
    try {
        const response = await api.get(`api/networks`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getNetworkService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/networks/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postNetworkService<T>(
    api: AxiosInstance,
    data: { network_code: string; network_name: string },
): Promise<T> {
    try {
        const response = await api.post(`api/networks`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function putNetworkService<T>(
    api: AxiosInstance,
    id: number,
    data: { network_name: string },
): Promise<T> {
    try {
        const response = await api.put(`api/networks/${id}`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delNetworkService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/networks/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Stations
export async function getStationInfoService<T>(
    api: AxiosInstance,
    params: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/station-info${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationInfoByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/station-info/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationInfoService<T>(
    api: AxiosInstance,
    body: object,
): Promise<T> {
    try {
        const response = await api.post("api/station-info", body);
        return response.data as Promise<T>;
    } catch (err) {
        return Promise.reject(err);
    }
}

export async function patchStationAttachedFileDescription<T>(
    api: AxiosInstance,
    body: object,
    id: number,
): Promise<T> {
    try {
        const response = await api.patch(
            "api/station-attached-files/" + id.toString(),
            body,
        );
        return response.data as Promise<T>;
    } catch (err) {
        return Promise.reject(err);
    }
}

export async function patchVisitAttachedFileDescription<T>(
    api: AxiosInstance,
    body: object,
    id: number,
): Promise<T> {
    try {
        const response = await api.patch(
            "api/visit-gnss-data-files/" + id.toString(),
            body,
        );
        return response.data as Promise<T>;
    } catch (err) {
        return Promise.reject(err);
    }
}

export async function patchVisitOthersFileDescription<T>(
    api: AxiosInstance,
    body: object,
    id: number,
): Promise<T> {
    try {
        const response = await api.patch(
            "api/visit-attached-files/" + id.toString(),
            body,
        );
        return response.data as Promise<T>;
    } catch (err) {
        return Promise.reject(err);
    }
}

export async function patchStationImagesDescription<T>(
    api: AxiosInstance,
    body: object,
    id: number,
): Promise<T> {
    try {
        const response = await api.patch(
            "api/station-images/" + id.toString(),
            body,
        );
        return response.data as Promise<T>;
    } catch (err) {
        return Promise.reject(err);
    }
}

export async function patchVisitImagesDescription<T>(
    api: AxiosInstance,
    body: object,
    id: number,
): Promise<T> {
    try {
        const response = await api.patch(
            "api/visit-images/" + id.toString(),
            body,
        );
        return response.data as Promise<T>;
    } catch (err) {
        return Promise.reject(err);
    }
}

export async function getTraceReceiverByRinex<T>(
    api: AxiosInstance,
    receiver_serial: string,
    receiver_type?: string,
    extraParams?: Record<string, any>,
): Promise<T> {
    try {
        const params = new URLSearchParams({ receiver_serial });
        if (receiver_type) params.append("receiver_type", receiver_type);
        if (extraParams) {
            Object.entries(extraParams).forEach(([k, v]) =>
                params.append(k, String(v)),
            );
        }
        const response = await api.get(`api/rinex?${params.toString()}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getTraceReceiverByStationInfo<T>(
    api: AxiosInstance,
    receiver_serial: string,
    receiver_code?: string,
    extraParams?: Record<string, any>,
): Promise<T> {
    try {
        const params = new URLSearchParams({ receiver_serial });
        if (receiver_code) params.append("receiver_code", receiver_code);
        if (extraParams) {
            Object.entries(extraParams).forEach(([k, v]) =>
                params.append(k, String(v)),
            );
        }
        const response = await api.get(`api/station-info?${params.toString()}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// mandas como form-data un archivo con key "file"

export async function postStationInfoByFileService<T>(
    api: AxiosInstance,
    station_api_id: number,
    data: FormData,
): Promise<T> {
    try {
        const response = await api.post(
            `api/station/${station_api_id}/insert-station-info-by-file`,
            data,
            {
                headers: {
                    "Content-Type": "multipart/form-data",
                },
            },
        );
        return response.data as Promise<T>;
    } catch (err) {
        return Promise.reject(err);
    }
}

export async function getRecordsFromFile<T>(
    api: AxiosInstance,
    id: number,
    body: object,
): Promise<T> {
    try {
        const response = await api.post(
            `/api/station/${id}/parse-station-info-by-file`,
            body,
            {
                headers: {
                    "Content-Type": "multipart/form-data",
                },
            },
        );
        return response.data as Promise<T>;
    } catch (err) {
        return Promise.reject(err);
    }
}

export async function putStationInfoService<T>(
    api: AxiosInstance,
    id: number,
    body: object,
): Promise<T> {
    try {
        const response = await api.put(`api/station-info/${id}`, body);
        return response.data as Promise<T>;
    } catch (err) {
        return Promise.reject(err);
    }
}

export async function delStationInfoService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/station-info/${id}`);
        return response.data as Promise<T>;
    } catch (err) {
        return Promise.reject(err);
    }
}

export async function getEarthquakeService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/earthquakes/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getEarthquakesService<T>(
    api: AxiosInstance,
    params?: EarthQuakeParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParamsForFilter(params) : "";
        const response = await api.get(
            `api/earthquakes${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getAffectedStationsService<T>(
    api: AxiosInstance,
    id?: number,
    options?: { signal?: AbortSignal },
): Promise<T> {
    try {
        const response = await api.get(
            `api/earthquakes/${id}/affected-stations`,
            { signal: options?.signal },
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getKmzFileService<T>(
    api: AxiosInstance,
    params: string,
): Promise<T> {
    try {
        const url = `api/stations/${params}/get-kmz`;
        const response = await api.get(url);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationsService<T>(
    api: AxiosInstance,
    params?: GetParams,
    options?: { signal?: AbortSignal },
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/stations${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
            { signal: options?.signal },
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/stations/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationService<T>(
    api: AxiosInstance,
    data: StationData,
): Promise<T> {
    try {
        const response = await api.post(`api/stations`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function patchStationService<T>(
    api: AxiosInstance,
    id: number,
    data: any,
): Promise<T> {
    try {
        const response = await api.patch(`api/stations/${id}`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delStationService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/stations/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationDetailsService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/stations/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationMetaService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/station-meta/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function patchStationMetaService<T>(
    api: AxiosInstance,
    id: number,
    data: any,
): Promise<T> {
    try {
        const response = await api.patch(`api/station-meta/${id}`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationsFilesAttachedService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/station-attached-files${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationFileByIdAttachedService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/station-attached-files/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationImagesService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/station-images${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationImageByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/station-images/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationsImagesService<T>(
    api: AxiosInstance,
    data: FormData,
): Promise<T> {
    try {
        const response = await api.post(`api/station-images`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delStationsImagesService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/station-images/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationsFilesAttachedService<T>(
    api: AxiosInstance,
    data: FormData,
): Promise<T> {
    try {
        const response = await api.post(`api/station-attached-files`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delStationsFilesAttachedService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/station-attached-files/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationCampaignsService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/campaigns${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationCampaignByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/campaigns/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationCampaignService<T>(
    api: AxiosInstance,
    data: any,
): Promise<T> {
    try {
        const response = await api.post(`api/campaigns`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function patchStationCampaignService<T>(
    api: AxiosInstance,
    id: number,
    data: any,
): Promise<T> {
    try {
        const response = await api.put(`api/campaigns/${id}`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delStationCampaignService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/campaigns/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationVisitsService<T>(
    api: AxiosInstance,
    params: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/visits${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationVisitsByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/visits/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationVisitService<T>(
    api: AxiosInstance,
    data: FormData,
): Promise<T> {
    try {
        const response = await api.post(`api/visits`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function patchStationVisitService<T>(
    api: AxiosInstance,
    id: number,
    data: FormData,
): Promise<T> {
    try {
        const response = await api.put(`api/visits/${id}`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationVisitsService<T>(
    api: AxiosInstance,
    data: FormData,
): Promise<T> {
    try {
        // {
        //     "date": "2024-08-20",
        //     "log_sheet_file": "string",
        //     "log_sheet_filename": "string",
        //     "navigation_file": "string",
        //     "navigation_filename": "string",
        //     "campaign": 0,
        //     "station": 0,
        //     "people": [
        //       0
        //     ]
        //   }

        const response = await api.post(`api/visits`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delStationVisitService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/visits/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationVisitsImagesService<T>(
    api: AxiosInstance,
    params: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/visit-images${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationVisitsImagesByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/visit-images/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationVisitsImagesService<T>(
    api: AxiosInstance,
    data: FormData,
): Promise<T> {
    try {
        const response = await api.post(`api/visit-images`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delStationVisitsImagesService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/visit-images/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationVisitGnssFilesService<T>(
    api: AxiosInstance,
    params: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";

        const response = await api.get(
            `api/visit-gnss-data-files${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationVisitGnssFileByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/visit-gnss-data-files/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationVisitGnssFilesService<T>(
    api: AxiosInstance,
    data: FormData,
): Promise<T> {
    try {
        // {
        //     "file": "string",
        //     "filename": "string",
        //     "description": "string",
        //     "visit": 0
        //   }

        const response = await api.post(`api/visit-gnss-data-files`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delStationVisitGnssFilesService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/visit-gnss-data-files/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationVisitFilesService<T>(
    api: AxiosInstance,
    params: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";

        const response = await api.get(
            `api/visit-attached-files${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationVisitFileByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/visit-attached-files/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationVisitFilesService<T>(
    api: AxiosInstance,
    data: FormData,
): Promise<T> {
    try {
        // {
        //     "file": "string",
        //     "filename": "string",
        //     "description": "string",
        //     "visit": 0
        //   }

        const response = await api.post(`api/visit-attached-files`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delStationVisitFilesService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/visit-attached-files/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Station Time Series

export async function resetTimeSeriesPolynomialService<T>(
    api: AxiosInstance,
    id: number,
    solution: string,
    stack: string,
): Promise<T> {
    try {
        const url = `api/time-series-config/${id}/${solution}/reset-polynomial${solution === "GAMIT" ? `?stack=${stack}` : ""}`;
        const response = await api.post(url);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function resetTimeSeriesPeriodicService<T>(
    api: AxiosInstance,
    id: number,
    solution: string,
    stack: string,
): Promise<T> {
    try {
        const url = `api/time-series-config/${id}/${solution}/reset-periodic${solution === "GAMIT" ? `?stack=${stack}` : ""}`;
        const response = await api.post(url);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function resetTimeSeriesJumpsService<T>(
    api: AxiosInstance,
    id: number,
    solution: string,
    stack: string,
): Promise<T> {
    try {
        const url = `api/time-series-config/${id}/${solution}/reset-jumps${solution === "GAMIT" ? `?stack=${stack}` : ""}`;
        const response = await api.post(url);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postTimeSeriesPolynomialService<T>(
    api: AxiosInstance,
    id: number,
    solution: string,
    stack: string,
    params: any,
): Promise<T> {
    try {
        const url = `api/time-series-config/${id}/${solution}/set-polynomial${solution === "GAMIT" ? `?stack=${stack}` : ""}`;
        const response = await api.post(url, {
            terms: Number(params.terms),
            Year: Number(params.Year),
            DOY: Number(params.DOY),
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postTimeSeriesPeriodicService<T>(
    api: AxiosInstance,
    id: number,
    solution: string,
    stack: string,
    params: any,
): Promise<T> {
    try {
        const url = `api/time-series-config/${id}/${solution}/set-periodic${solution === "GAMIT" ? `?stack=${stack}` : ""}`;
        const response = await api.post(url, params);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postTimeSeriesJumpService<T>(
    api: AxiosInstance,
    id: number,
    solution: string,
    stack: string,
    params: any,
): Promise<T> {
    try {
        const url = `api/time-series-config/${id}/${solution}/set-jumps${solution === "GAMIT" ? `?stack=${stack}` : ""}`;
        const response = await api.post(url, params);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getJumpTypesService<T>(api: AxiosInstance): Promise<T> {
    try {
        const response = await api.get(
            `api/time-series-config/available-jump-types`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function deleteTimeSeriesJumpService<T>(
    api: AxiosInstance,
    id: number,
    solution: string,
    params: any,
): Promise<T> {
    try {
        const response = await api.post(
            `api/time-series-config/${id}/${solution}/delete-jump`,
            params,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationTimeSeriesService<T>(
    api: AxiosInstance,
    id: number,
    params: any,
    json: boolean,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/time-series/${id}${paramsArr.length > 0 ? `?${paramsArr}&json=${json}` : `?json=${json}`}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStackNamesService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/distinct-stack-names/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Station Events

export async function getStationEventsService<T>(
    api: AxiosInstance,
    params: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/events${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationEventByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/events/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Antennas
export async function getAntennasService<T>(api: AxiosInstance): Promise<T> {
    try {
        const response = await api.get(`api/antennas`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getAntennaService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/antennas/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postAntennaService<T>(
    api: AxiosInstance,
    data: { antenna_code: string; antenna_description: string },
): Promise<T> {
    try {
        const response = await api.post(`api/antennas`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function putAntennaService<T>(
    api: AxiosInstance,
    id: number,
    data: { antenna_description: string }, // FIXME:
): Promise<T> {
    try {
        const response = await api.put(`api/antennas/${id}`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delAntennaService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/antennas/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Receivers

export async function getReceiversService<T>(api: AxiosInstance): Promise<T> {
    try {
        const response = await api.get(`api/receivers`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getReceiverService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/receivers/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postReceiverService<T>(
    api: AxiosInstance,
    data: { receiver_code: string; receiver_description: string },
): Promise<T> {
    try {
        const response = await api.post(`api/receivers`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function putReceiverService<T>(
    api: AxiosInstance,
    id: number,
    data: { receiver_description: string }, // FIXME:
): Promise<T> {
    try {
        const response = await api.put(`api/receivers/${id}`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delReceiverService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/receivers/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Height Codes

export async function getHeightCodesService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/gamit-htc${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Countries

export async function getCompletionPlotService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/rinex-completion-plot/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getCountriesService<T>(api: AxiosInstance): Promise<T> {
    try {
        const response = await api.get(`api/countries`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getCountryService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/countries/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Rinex

export async function getRinexService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/rinex${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getRinexWithStatusService<T>(
    api: AxiosInstance,
    station_api_id: number,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/get-rinex-with-status/${station_api_id}${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postExtendUpRinexService<T>(
    api: AxiosInstance,
    rinex_api_id: number,
): Promise<T> {
    try {
        const response = await api.get(
            `api/rinex/${rinex_api_id}/get-next-station-info`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postExtendDownRinexService<T>(
    api: AxiosInstance,
    rinex_api_id: number,
): Promise<T> {
    try {
        const response = await api.get(
            `api/rinex/${rinex_api_id}/get-previous-station-info`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Overview

// Monuments CRUD
export async function getMonumentsTypesService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/monument-types${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getMonumentsTypesByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/monument-types/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postMonumentTypesService<T>(
    api: AxiosInstance,
    data: FormData,
): Promise<T> {
    try {
        const response = await api.post(`api/monument-types`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function patchMonumentTypesService<T>(
    api: AxiosInstance,
    id: number,
    data: FormData | any,
): Promise<T> {
    try {
        const response = await api.patch(`api/monument-types/${id}`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delMonumentTypesService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/monument-types/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationStatusColorsService<T>(
    api: AxiosInstance,
): Promise<T> {
    try {
        const response = await api.get("api/station-status-color");
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationStatusService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/station-status${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationStatusByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/station-status/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationStatusService<T>(
    api: AxiosInstance,
    data: { name: string },
): Promise<T> {
    try {
        const response = await api.post(`api/station-status`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function patchStationStatusService<T>(
    api: AxiosInstance,
    id: number,
    data: { name: string },
): Promise<T> {
    try {
        const response = await api.patch(`api/station-status/${id}`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delStationStatusService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/station-status/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Station Roles CRUD

export async function getStationRolesService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/station-roles${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationRolesService<T>(
    api: AxiosInstance,
    data: { name: string },
): Promise<T> {
    try {
        const response = await api.post(`api/station-roles`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function patchStationRolesService<T>(
    api: AxiosInstance,
    id: number,
    data: { name: string },
): Promise<T> {
    try {
        const response = await api.patch(`api/station-roles/${id}`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postCreateStationService<T>(
    api: AxiosInstance,
    data: {
        network_code: string;
        station_code: string;
        auto_x: string;
        auto_y: string;
        auto_z: string;
        lat: string;
        lon: string;
        height: string;
        max_dist: string;
        dome: string;
    },
): Promise<T> {
    try {
        const response = await api.post(`api/stations`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delStationRolesService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/station-roles/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Station Types CRUD

export async function getStationTypesService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/station-types${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getStationTypesByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/station-types/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postStationTypesService<T>(
    api: AxiosInstance,
    data: FormData,
): Promise<T> {
    try {
        const response = await api.post(`api/station-types`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function patchStationTypesService<T>(
    api: AxiosInstance,
    id: number,
    data: FormData,
): Promise<T> {
    try {
        const response = await api.patch(`api/station-types/${id}`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delStationTypesService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/station-types/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// People CRUD

export async function getPeopleService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/people${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function mergePeopleService<T>(
    api: AxiosInstance,
    params: { from: number; to: number },
): Promise<T> {
    try {
        const url = `api/people/${params.from}/merge-to/${params.to}`;
        const response = await api.post(url);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function mergeSourcesServersService<T>(
    api: AxiosInstance,
    params: { from: number; to: number },
): Promise<T> {
    try {
        const url = `api/sources-servers/${params.from}/merge-to/${params.to}`;
        const response = await api.post(url);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function swapTryOrdenService<T>(
    api: AxiosInstance,
    params: { from: number; to: number },
): Promise<T> {
    try {
        const url = `api/sources-stations/${params.from}/swap-try-order/${params.to}`;
        const response = await api.post(url);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// export async function getPeopleByName<T>(
//     api: AxiosInstance,
//     first_name: string | undefined,
//     last_name: string | undefined,
// ): Promise<T> {
//     try {
//         const response = await api.get(
//             `api/people/has-duplicates/${first_name ?? ""}/${last_name ?? ""}`,
//         );
//         return response.data as Promise<T>;
//     } catch (error) {
//         return Promise.reject(error);
//     }
// }

export async function postPeopleService<T>(
    api: AxiosInstance,
    data: FormData,
): Promise<T> {
    try {
        const response = await api.post(`api/people`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function patchPeopleService<T>(
    api: AxiosInstance,
    id: number,
    data: FormData | any,
): Promise<T> {
    try {
        const response = await api.patch(`api/people/${id}`, data, {
            headers: {
                "Content-Type": "multipart/form-data",
            },
        });
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delPeopleService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/people/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// FIN Overview

// CRD role-person-station

export async function getRolePersonStationService<T>(
    api: AxiosInstance,
    params?: GetParams,
): Promise<T> {
    try {
        const paramsArr = params ? transformParams(params) : "";
        const response = await api.get(
            `api/role-person-station${paramsArr.length > 0 ? `?${paramsArr}` : ""}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getPersonRelations<T>(
    api: AxiosInstance,
    person_id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/people/${person_id}/relations`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postRolePersonStationService<T>(
    api: AxiosInstance,
    data: { role: string; person: string },
): Promise<T> {
    try {
        const response = await api.post(`api/role-person-station`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function delRolePersonStationService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/role-person-station/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Sources Servers CRUD
export async function getSourcesServersByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/sources-servers/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getSourcesServersService<T>(
    api: AxiosInstance,
): Promise<T> {
    try {
        const response = await api.get(`api/sources-servers`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postSourcesServersService<T>(
    api: AxiosInstance,
    data: any,
): Promise<T> {
    try {
        const response = await api.post(`api/sources-servers`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function putSourcesServersService<T>(
    api: AxiosInstance,
    id: number,
    data: any,
): Promise<T> {
    try {
        const response = await api.put(`api/sources-servers/${id}`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function deleteSourcesServersService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/sources-servers/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Sources Formats CRUD
export async function getSourcesFormatsByIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/sources-formats/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getSourcesFormatsService<T>(
    api: AxiosInstance,
): Promise<T> {
    try {
        const response = await api.get(`api/sources-formats`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postSourcesFormatsService<T>(
    api: AxiosInstance,
    data: any,
): Promise<T> {
    try {
        const response = await api.post(`api/sources-formats`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function putSourcesFormatsService<T>(
    api: AxiosInstance,
    id: number,
    data: any,
): Promise<T> {
    try {
        const response = await api.put(`api/sources-formats/${id}`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function deleteSourcesFormatsService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/sources-formats/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

// Sources Stations CRUD
export async function getSourcesStationsByServerIdService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.get(`api/sources-stations?server_id=${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getSourcesStationsByStationIdService<T>(
    api: AxiosInstance,
    nc: string,
    sc: string,
): Promise<T> {
    try {
        const response = await api.get(
            `api/sources-stations?network_code=${nc}&station_code=${sc}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getSourcesStationsService<T>(
    api: AxiosInstance,
): Promise<T> {
    try {
        const response = await api.get(`api/sources-stations`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function postSourcesStationsService<T>(
    api: AxiosInstance,
    data: any,
): Promise<T> {
    try {
        const response = await api.post(`api/sources-stations`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function putSourcesStationsService<T>(
    api: AxiosInstance,
    id: number,
    data: any,
): Promise<T> {
    try {
        const response = await api.put(`api/sources-stations/${id}`, data);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function deleteSourcesStationsService<T>(
    api: AxiosInstance,
    id: number,
): Promise<T> {
    try {
        const response = await api.delete(`api/sources-stations/${id}`);
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function removeEarthquakesAffectedStationsCache<T>(
    api: AxiosInstance,
): Promise<T> {
    try {
        const response = await api.post(
            `api/remove-earthquakes-affected-stations-cache`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

export async function getNearbyStations<T>(
    api: AxiosInstance,
    station_id: number,
    radius_km: number,
): Promise<T> {
    try {
        const response = await api.get(
            `api/stations/${station_id}/nearby-stations/${radius_km}`,
        );
        return response.data as Promise<T>;
    } catch (error) {
        return Promise.reject(error);
    }
}

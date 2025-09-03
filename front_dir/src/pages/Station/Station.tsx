import {
    NavigationType,
    Outlet,
    useLocation,
    useNavigate,
    useParams,
} from "react-router-dom";

import React, { useEffect, useMemo, useState } from "react";

import {
    Sidebar,
    StationButtons,
    Skeleton,
    Breadcrumb,
    Toast,
    StationCommentsModal,
} from "@componentsReact";
import { router } from "App";

import { useAuth, useApi } from "@hooks";

import { generateErrorMessages, hasDifferences, showModal } from "@utils";

import {
    getStationImagesService,
    getStationMetaService,
    getStationsService,
    getStationVisitsService,
    getKmzFileService,
} from "@services";

import {
    Errors,
    StationData,
    StationImagesData,
    StationImagesServiceData,
    StationMetadataServiceData,
    StationServiceData,
    StationVisitsData,
    StationVisitsServiceData,
    KmzFile,
} from "@types";

import { AxiosError } from "axios";

const Station = () => {
    const { sc, nc } = useParams<{ sc: string; nc: string }>();

    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const navigate = useNavigate();

    const location = useLocation();

    const locationState = location.state as StationData;

    const isMainLocation =
        location.pathname === `/${nc}/${sc}` ||
        location.pathname === `/${nc}/${sc}/`;

    const [message, setMessage] = useState<{
        error: boolean | undefined;
        msg: string;
        errors?: Errors;
    }>({ error: undefined, msg: "", errors: undefined });

    const [station, setStation] = useState<StationData | undefined>(undefined);
    const [reStation, setReStation] = useState<StationData | undefined>(
        undefined,
    );

    const [stationMeta, setStationMeta] = useState<
        StationMetadataServiceData | undefined
    >(undefined);

    const [images, setImages] = useState<StationImagesData[] | undefined>(
        undefined,
    );

    const [visits, setVisits] = useState<StationVisitsData[] | undefined>(
        undefined,
    );

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);
    const [loading, setLoading] = useState<boolean>(true);
    const [reLoading, setReLoading] = useState<boolean>(false);
    const [photoLoading, setPhotoLoading] = useState<boolean>(true);

    const [stationLocationScreen, setStationLocationScreen] =
        useState<string>("");

    const [stationLocationDetailScreen, setStationLocationDetailScreen] =
        useState<string>("");

    const [loadPdf, setLoadPdf] = useState<boolean>(false);
    const [loadedMap, setLoadedMap] = useState<boolean | undefined>(undefined);
    const [loadedPdfData, setLoadedPdfData] = useState<boolean | undefined>(
        undefined,
    );

    const [kmzFile, setKmzFile] = useState<string | undefined>(undefined);

    const stationTitle = station
        ? station?.network_code?.toUpperCase() +
          "." +
          station?.station_code?.toUpperCase()
        : "Station not found";

    const getStation = async () => {
        try {
            setLoading(true);
            const res = await getStationsService<StationServiceData>(api, {
                network_code: nc,
                station_code: sc,
                limit: 1,
                offset: 0,
            });
            setStation(res.data[0]);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const getReStation = async () => {
        try {
            getStationMeta();
            setReLoading(true);
            closeToast();
            const res = await getStationsService<StationServiceData>(api, {
                network_code: nc,
                station_code: sc,
                limit: 1,
                offset: 0,
            });
            setReStation(res.data[0]);
            setMessage({
                error: false,
                msg: "Station refetched successfully",
            });
        } catch (error: unknown) {
            if (error instanceof AxiosError) {
                const apiErrorResponse = error.response?.data as Errors;
                setMessage({
                    error: true,
                    msg: error.message,
                    errors: apiErrorResponse,
                });
                console.error(error);
            }
        } finally {
            setReLoading(false);
        }
    };

    const getStationMeta = async () => {
        try {
            const res = await getStationMetaService<StationMetadataServiceData>(
                api,
                Number(station?.api_id ?? locationState.api_id),
            );
            if (res) {
                setStationMeta(res);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const getStationImages = async () => {
        try {
            setPhotoLoading(true);
            const result =
                await getStationImagesService<StationImagesServiceData>(api, {
                    offset: 0,
                    limit: 0,
                    station_api_id: String(
                        station?.api_id ?? locationState.api_id,
                    ),
                    thumbnail: true,
                });

            if (result) {
                setImages(result.data);
            }
        } catch (err) {
            console.error(err);
        } finally {
            setPhotoLoading(false);
        }
    };

    const getVisits = async () => {
        try {
            const res = await getStationVisitsService<StationVisitsServiceData>(
                api,
                {
                    limit: 0,
                    offset: 0,
                    station_api_id: String(
                        station?.api_id ?? locationState.api_id,
                    ),
                },
            );

            if (res.statusCode === 200) {
                setVisits(res.data);
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    const getKmzBalloon = async () => {
        try {
            if (station?.api_id) {
                const res = await getKmzFileService<KmzFile>(
                    api,
                    station?.api_id.toString(),
                );
                if (res.statusCode == 200) {
                    setKmzFile(res.kmz);
                }
            }
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const getButtonClasses = () => {
        const baseClasses = "hover:scale-110 btn-ghost rounded-lg p-1";
        let additionalClasses = "";

        if (
            location.pathname !== `/${nc}/${sc}` &&
            location.pathname !== `/${nc}/${sc}/rinex`
        ) {
            additionalClasses = "mr-24";
        } else if (location.pathname === `/${nc}/${sc}`) {
            additionalClasses = "mr-0";
        } else if (
            errorMessages.length === 0 &&
            location.pathname === `/${nc}/${sc}/rinex`
        ) {
            additionalClasses = "mr-24";
        } else if (
            errorMessages.length > 0 &&
            location.pathname === `/${nc}/${sc}/rinex`
        ) {
            additionalClasses = "mr-20";
        }

        return `${baseClasses} ${additionalClasses}`;
    };

    const refetch = () => {
        getStation();
        setVisits(undefined);
        setStationMeta(undefined);
        setLoadedMap(undefined);
    };

    const closeToast = () => {
        setMessage({ error: undefined, msg: "" });
    };

    const errorMessages = useMemo(() => {
        if (station && reStation && hasDifferences(station, reStation)) {
            return generateErrorMessages(reStation);
        } else if (station) {
            return generateErrorMessages(station);
        }
        return [];
    }, [station, reStation]);

    useEffect(() => {
        if (locationState && !loading && !station) {
            setStation(locationState);
        } else if (!station) {
            getStation();
        }
    }, [locationState, station]); //eslint-disable-line

    useEffect(() => {
        // This effect is used to handle the case when the user
        // renders the page and the station is already in the state.

        if (station) {
            if (isMainLocation) {
                getStationImages();
                // getVisits() bcs it is called on visits page and when user
                // navigates back to station page he should see the new visits
                getVisits();
            }
            getStationMeta();
        }
        // eslint-disable-next-line
    }, [station, isMainLocation]);

    useEffect(() => {
        if (station) {
            // if (location.pathname === `/${nc}/${sc}`) {
            //     getVisits();
            // }
            setLoadPdf(false);
            setLoadedPdfData(undefined);
            setLoadedMap(undefined);
        }
    }, [location, station]);

    useEffect(() => {
        // This effect is used to handle the case when te user
        // navigates back to main page.
        if (station) {
            const unsubscribe = router.subscribe((state) => {
                if (state.historyAction === NavigationType.Pop) {
                    if (state.location.pathname === "/") {
                        navigate("/temp", { replace: true, state: {} });
                        setTimeout(() => {
                            navigate("/", {
                                state: {
                                    ...locationState,
                                    mainParams:
                                        locationState.mainParams ?? undefined,
                                },
                            });
                        }, 0);
                    }
                }
            });

            return () => {
                unsubscribe();
            };
        }
    }, [station, locationState, navigate]);

    useEffect(() => {
        if (kmzFile !== undefined && kmzFile !== "") {
            const byteCharacters = atob(kmzFile);
            const byteNumbers = new Array(byteCharacters.length);
            for (let i = 0; i < byteCharacters.length; i++) {
                byteNumbers[i] = byteCharacters.charCodeAt(i);
            }
            const byteArray = new Uint8Array(byteNumbers);

            const blob = new Blob([byteArray], {
                type: "application/octet-stream",
            });

            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            link.download = `${station?.network_code}_${station?.station_code}.kmz`;

            document.body.appendChild(link);
            link.click();

            document.body.removeChild(link);
            URL.revokeObjectURL(link.href);
        }
    }, [kmzFile]);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    return (
        <div className="max-h-[92vh] transition-all duration-200">
            {typeof message.error === "boolean" &&
                message.error !== undefined && (
                    <Toast
                        error={message.error}
                        msg={
                            !message.errors
                                ? message.msg
                                : message.errors.errors[0].code === "blank"
                                  ? "Fields may not be blank."
                                  : message.errors.errors[0].detail
                        }
                    />
                )}
            {loading ? (
                <div className="mt-24">
                    <Skeleton />
                </div>
            ) : (
                <div className="flex w-full">
                    <Sidebar
                        station={
                            station &&
                            reStation &&
                            hasDifferences(station, reStation)
                                ? reStation
                                : station
                        }
                        mainParams={locationState?.mainParams ?? undefined}
                        stationMeta={stationMeta}
                        refetchStationMeta={getStationMeta}
                        refetch={refetch}
                    />
                    <Breadcrumb
                        state={
                            station &&
                            reStation &&
                            hasDifferences(station, reStation)
                                ? {
                                      ...reStation,
                                      mainParams:
                                          locationState?.mainParams ??
                                          undefined,
                                  }
                                : station
                                  ? {
                                        ...station,
                                        mainParams:
                                            locationState?.mainParams ??
                                            undefined,
                                    }
                                  : locationState
                        }
                        setters={{
                            setStationMeta,
                            setVisits,
                        }}
                    />
                    <div className="w-full flex flex-col pt-20">
                        <div className="flex relative self-center gap-2">
                            <h1 className="text-6xl font-bold text-center flex items-center justify-center">
                                {stationTitle}
                            </h1>
                            {station && (
                                <StationButtons
                                    functions={{
                                        setMessage,
                                        setLoadPdf,
                                        setLoadedPdfData,
                                        getButtonClasses,
                                        getKmzBalloon,
                                        getReStation,
                                        setModals,
                                    }}
                                    constants={{
                                        station,
                                        reLoading,
                                        reStation,
                                        stationMeta,
                                        visits,
                                        loadPdf,
                                        loadedMap,
                                        errorMessages,
                                        stationLocationScreen,
                                        stationLocationDetailScreen,
                                    }}
                                />
                            )}
                        </div>

                        {modals && modals.title === "StationComments" && (
                            <StationCommentsModal
                                visits={visits}
                                stationMeta={stationMeta}
                                onHide={() => setModals(undefined)}
                                setModal={setModals}
                                station={station}
                            />
                        )}

                        <Outlet
                            context={{
                                station,
                                reStation,
                                stationMeta,
                                images,
                                photoLoading,
                                loadPdf,
                                loadedMap,
                                loadedPdfData,
                                visits,
                                getStationImages,
                                getReStation,
                                setStationLocationScreen,
                                setStationLocationDetailScreen,
                                setLoadPdf,
                                setLoadedMap,
                            }}
                        />
                    </div>{" "}
                </div>
            )}
        </div>
    );
};

export default React.memo(Station);

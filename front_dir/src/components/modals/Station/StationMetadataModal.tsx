import { useEffect, useMemo, useRef, useState } from "react";

import {
    Alert,
    ConfirmDeleteModal,
    LargeSkeleton,
    Modal,
    RenderFileModal,
    StationAddFileModal,
    QuillText,
    Dropzone,
} from "@componentsReact";

import {
    useApi,
    useAuth,
    useFormReducer,
    usePopup,
    useWaitCursor,
} from "@hooks";

import {
    ArrowDownTrayIcon,
    BookOpenIcon,
    ClipboardDocumentIcon,
    PencilSquareIcon,
    PlusCircleIcon,
    TrashIcon,
} from "@heroicons/react/24/outline";

import defPhoto from "@assets/images/placeholder.png";

import {
    delStationsFilesAttachedService,
    getMonumentsTypesService,
    getMonumentsTypesByIdService,
    getRinexService,
    getStationFileByIdAttachedService,
    getStationsFilesAttachedService,
    getStationInfoService,
    getStationStatusService,
    getStationTypesService,
    patchStationMetaService,
    patchStationService,
    getStationsService,
    getStationMetaService,
} from "@services";

import { classHtml, decimalToDMS, formattedDates, showModal } from "@utils";

import {
    ErrorResponse,
    Errors,
    ExtendedStationData,
    MonumentTypes,
    MonumentTypesServiceData,
    RinexData,
    RinexServiceData,
    StationData,
    StationFilesData,
    StationFilesServiceData,
    StationInfoData,
    StationInfoServiceData,
    StationMetadataServiceData,
    StationServiceData,
    StationStatus,
    StationStatusServiceData,
} from "@types";

interface StationMetadataProps {
    close: boolean;
    size?: "sm" | "md" | "lg" | "xl" | "fit";
    station?: StationData | undefined;
    stationMetaMain?: StationMetadataServiceData | undefined;
    setModalState: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
    refetch: () => void;
}

const StationMetadataModal = ({
    close,
    station,
    stationMetaMain,
    size,
    refetch,
    setModalState,
}: StationMetadataProps) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const { show, showPopup } = usePopup(2000);

    const [copyId, setCopyId] = useState<string | null>(null);

    const [oceanTideType, setOceanTideType] = useState<
        "by file" | "manual" | undefined
    >(undefined);

    const [metaMsg, setMetaMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);
    const [stationMsg, setStationMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [fileMsg, setFileMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [loading, setLoading] = useState<boolean>(true);
    const [loadFile, setLoadFile] = useState<boolean>(false);

    const [oceanTideFile, setOceanTideFile] = useState<File | undefined>(
        undefined,
    );

    useWaitCursor(loadFile);

    const [updateLoading, setUpdateLoading] = useState<boolean>(false);
    const [edit, setEdit] = useState<boolean>(false);

    const [monumentType, setMonumentType] = useState<MonumentTypes[]>([]);

    const [chosenMonumentPhoto, setChosenMonumentPhoto] = useState<
        string | null
    >(null);

    const [fileToEdit, setFileToEdit] = useState<
        StationFilesData | undefined
    >();

    const [stationStatus, setStationStatus] = useState<StationStatus[]>([]);

    const [stationType, setStationType] = useState<StationStatus[]>([]);

    const [firstRinex, setFirstRinex] = useState<RinexData | undefined>(
        undefined,
    );
    const [lastRinex, setLastRinex] = useState<RinexData | undefined>(
        undefined,
    );

    const [stationData, setStationData] = useState<StationData | undefined>(
        undefined,
    );

    const [stationMeta, setStationMeta] = useState<
        StationMetadataServiceData | undefined
    >(undefined);

    const [stationInfo, setStationInfo] = useState<StationInfoData | undefined>(
        undefined,
    );

    const [richText, setRichText] = useState<string>(
        stationMetaMain?.comments ?? "",
    );

    const [fileType, setFileType] = useState<"meta" | "none">("none");

    const [files, setFiles] = useState<StationFilesData[] | undefined>(
        undefined,
    );

    const [fileToDel, setFileToDel] = useState<number | undefined>(undefined);

    const [fileToShow, setFileToShow] = useState<StationFilesData | undefined>(
        undefined,
    );

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [showAllFiles, setShowAllFiles] = useState<boolean>(false);

    const handleShowMore = () => {
        setShowAllFiles(true);
    };

    const handleShowLess = () => {
        setShowAllFiles(false);
    };

    const getStation = async () => {
        try {
            const res = await getStationsService<StationServiceData>(api, {
                network_code: station?.network_code,
                station_code: station?.station_code,
                limit: 1,
                offset: 0,
            });
            setStationData(res.data[0]);
        } catch (e) {
            console.error(e);
        }
    };

    const getStationMeta = async () => {
        try {
            const res = await getStationMetaService<StationMetadataServiceData>(
                api,
                Number(station?.api_id ?? undefined),
            );
            if (res) {
                setStationMeta(res);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const getTypes = async () => {
        try {
            if (stationMetaMain) {
                const status =
                    await getStationStatusService<StationStatusServiceData>(
                        api,
                    );

                const types =
                    await getStationTypesService<StationStatusServiceData>(api);

                const params = { only_metadata: true };
                const monuments =
                    await getMonumentsTypesService<MonumentTypesServiceData>(
                        api,
                        params,
                    );
                if (monuments.data.length > 0) {
                    const auxMonumentType = monuments.data;
                    if (auxMonumentType) {
                        const monumentId = auxMonumentType.find(
                            (mt) =>
                                mt.id ===
                                Number(stationMetaMain?.monument_type),
                        )?.id;
                        await getMonumentPhotoById(monumentId);
                    }
                }

                const stationTypes = types.data.sort((a, b) =>
                    a.name.localeCompare(b.name),
                );
                const stationStatus = status.data.sort((a, b) =>
                    a.name.localeCompare(b.name),
                );
                const monumentTypes = monuments.data.sort((a, b) =>
                    a.name.localeCompare(b.name),
                );

                setStationType(stationTypes ?? []);
                setMonumentType(monumentTypes ?? []);
                setStationStatus(stationStatus ?? []);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const getRinex = async () => {
        try {
            const firstRes = await getRinexService<RinexServiceData>(api, {
                network_code: station?.network_code,
                station_code: station?.station_code,
                limit: 1,
                offset: 0,
            });
            const totalRecords = firstRes.total_count;
            const lastRes = await getRinexService<RinexServiceData>(api, {
                network_code: station?.network_code,
                station_code: station?.station_code,
                limit: 1,
                offset: totalRecords - 1,
            });
            setFirstRinex(firstRes.data[0]);
            setLastRinex(lastRes.data[0]);
        } catch (err) {
            console.error(err);
        }
    };

    const getStationInfo = async () => {
        try {
            const res = await getStationInfoService<StationInfoServiceData>(
                api,
                {
                    network_code: station?.network_code ?? "",
                    station_code: station?.station_code ?? "",
                    offset: 0,
                    limit: 0,
                },
            );

            setStationInfo(
                res.data.sort(
                    (a, b) =>
                        new Date(b.date_end).getTime() -
                        new Date(a.date_end).getTime(),
                )[0],
            );
        } catch (err) {
            console.error(err);
        }
    };

    const stationId = stationMeta?.station ?? undefined;

    const getFiles = async () => {
        try {
            if (stationId) {
                const res =
                    await getStationsFilesAttachedService<StationFilesServiceData>(
                        api,
                        {
                            station_api_id: stationId,
                            offset: 0,
                            limit: 0,
                            only_metadata: true,
                        },
                    );
                setFiles(res.data);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const getFileById = async (id: number) => {
        try {
            setLoadFile(true);
            if (stationId) {
                const res =
                    await getStationFileByIdAttachedService<StationFilesData>(
                        api,
                        id,
                    );
                return res;
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoadFile(false);
        }
    };

    const getMonumentPhotoById = async (id: number | undefined) => {
        try {
            if (id) {
                const res = await getMonumentsTypesByIdService<MonumentTypes>(
                    api,
                    id,
                );
                setChosenMonumentPhoto(res.photo_file);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const delFile = async (id: number | undefined) => {
        try {
            setLoading(true);
            if (stationId && typeof id === "number") {
                const res =
                    await delStationsFilesAttachedService<ErrorResponse>(
                        api,
                        id,
                    );

                if ("status" in res && res.status !== "success") {
                    setFileMsg({
                        status: res.statusCode,
                        msg: res.response.type,
                        errors: res.response,
                    });
                } else {
                    setFileMsg({
                        status: res.statusCode,
                        msg: "File deleted successfully",
                    });
                    getFiles();
                }
            } else {
                setFileMsg({
                    status: 400,
                    msg: "File not found",
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const delFileMeta = async () => {
        try {
            setLoading(true);
            if (stationId) {
                const formData = new FormData();

                formData.append("navigation_file_delete", "true");
                formData.append("station", String(stationId));

                const res = await patchStationMetaService<
                    StationFilesData | ErrorResponse
                >(api, Number(stationMetaMain?.station), formData);
                if (res.statusCode !== 200 && "status" in res) {
                    setFileMsg({
                        status: res.statusCode,
                        msg: res.response.type,
                        errors: res.response,
                    });
                } else if (res.statusCode === 200) {
                    setFileMsg({
                        status: res.statusCode,
                        msg: "File deleted successfully",
                    });
                    getStationMeta();
                    // refetchStationMeta && refetchStationMeta();
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    function lla2ecef(llaArr: number[]): { x: number; y: number; z: number } {
        const [lat, lon, alt] = llaArr;

        // Convertir a radianes
        const rad_lat = (lat * Math.PI) / 180;
        const rad_lon = (lon * Math.PI) / 180;

        // Parámetros WGS84
        const a = 6378137.0;
        const finv = 298.257223563;
        const f = 1 / finv;
        const e2 = 1 - (1 - f) * (1 - f);

        const v = a / Math.sqrt(1 - e2 * Math.pow(Math.sin(rad_lat), 2));

        const x = (v + alt) * Math.cos(rad_lat) * Math.cos(rad_lon);
        const y = (v + alt) * Math.cos(rad_lat) * Math.sin(rad_lon);
        const z = (v * (1 - e2) + alt) * Math.sin(rad_lat);

        // Redondear a 8 decimales
        return {
            x: parseFloat(x.toFixed(3)),
            y: parseFloat(y.toFixed(3)),
            z: parseFloat(z.toFixed(3)),
        };
    }

    function ecef2lla(ecefArr: number[]): {
        lat: number;
        lon: number;
        alt: number;
    } {
        const [x, y, z] = ecefArr;

        // Parámetros WGS84
        const a = 6378137; // Semieje mayor (m)
        const e = 8.1819190842622e-2; // Excentricidad

        const asq = Math.pow(a, 2);
        const esq = Math.pow(e, 2);

        const b = Math.sqrt(asq * (1 - esq));
        const bsq = Math.pow(b, 2);

        const ep = Math.sqrt((asq - bsq) / bsq);
        const p = Math.sqrt(Math.pow(x, 2) + Math.pow(y, 2));
        const th = Math.atan2(a * z, b * p);

        const lon = Math.atan2(y, x);
        const lat = Math.atan2(
            z + Math.pow(ep, 2) * b * Math.pow(Math.sin(th), 3),
            p - esq * a * Math.pow(Math.cos(th), 3),
        );

        const N = a / Math.sqrt(1 - esq * Math.pow(Math.sin(lat), 2));
        const alt = p / Math.cos(lat) - N;

        // Convertir a grados y redondear a 8 decimales
        return {
            lat: parseFloat(((lat * 180) / Math.PI).toFixed(8)),
            lon: parseFloat(((lon * 180) / Math.PI).toFixed(8)),
            alt: parseFloat(alt.toFixed(3)),
        };
    }

    useEffect(() => {
        if (stationMeta && station) {
            getStationInfo();
            getFiles();
        }
    }, [stationMeta, station]);

    useEffect(() => {
        Promise.all([
            setLoading(true),
            getTypes(),
            getRinex(),
            getStationMeta(),
            getStation(),
        ]).then(() => {
            setLoading(false);
        });
    }, []);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    useEffect(() => {
        const updatedRichText = classHtml(richText);

        dispatch({
            type: "change_value",
            payload: {
                inputName: "rinex.comments",
                inputValue: updatedRichText,
            },
        });
    }, [richText]);

    useEffect(() => {
        if (fileToShow !== undefined) {
            setModals({
                show: true,
                title: "FileRender",
                type: "edit",
            });
        }
    }, [fileToShow]);

    const formattedData = useMemo(() => {
        return {
            equipment: {
                antenna_code: stationInfo?.antenna_code ?? "",
                antenna_serial: stationInfo?.antenna_serial ?? "",
                height_code: stationInfo?.height_code ?? "",
                receiver_code: stationInfo?.receiver_code ?? "",
                receiver_serial: stationInfo?.receiver_serial ?? "",
                receiver_version: stationInfo?.receiver_vers ?? "",
                radome_code: stationInfo?.radome_code ?? "",
            },
            rinex: {
                first_rinex: firstRinex?.observation_e_time ?? "",
                last_rinex: lastRinex?.observation_e_time ?? "",
                comments: stationMeta?.comments ?? "",
                navigation_file: stationMeta?.navigation_filename ?? "",
            },
            booleans: {
                has_battery: stationMeta?.has_battery ?? false,
                has_communications: stationMeta?.has_communications ?? false,
            },
            booleansDesc: {
                battery_description: stationMeta?.battery_description ?? "",
                communications_description:
                    stationMeta?.communications_description ?? "",
            },
            stationMeta: {
                station_type:
                    stationType.find(
                        (st) => st.id === Number(stationMeta?.station_type),
                    )?.name ?? "",
                monument_type:
                    monumentType.find(
                        (mt) => mt.id === Number(stationMeta?.monument_type),
                    )?.name ?? "",
                status:
                    stationStatus.find(
                        (st) => st.id === Number(stationMeta?.status),
                    )?.name ?? "",
                remote_access_link: stationMeta?.remote_access_link ?? "",
                station_name: stationData?.station_name ?? "",
                dome: stationData?.dome ?? "",
                harpos_coeff_otl: stationData?.harpos_coeff_otl ?? "",
                max_dist: String(stationData?.max_dist ?? ""),
            },

            station: {
                lat: String(Number(stationData?.lat).toFixed(8)) ?? "",
                lon: String(Number(stationData?.lon).toFixed(8)) ?? "",
                height: String(Number(stationData?.height).toFixed(3)) ?? "",
                auto_x: String(Number(stationData?.auto_x).toFixed(3)) ?? "",
                auto_y: String(Number(stationData?.auto_y).toFixed(3)) ?? "",
                auto_z: String(Number(stationData?.auto_z).toFixed(3)) ?? "",
            },
        };
    }, [stationType, monumentType, stationStatus, stationData, stationMeta]);

    const { formState, dispatch } = useFormReducer(formattedData);
    useEffect(() => {
        dispatch({
            type: "set",
            payload: formattedData,
        });
    }, [formattedData]);

    useEffect(() => {
        // useEffect to set monumentType photo dinamically on edit monument type
        if (monumentType.length > 0 && edit) {
            const newMonumentSelected = monumentType.find(
                (mt) => mt.name === formState.stationMeta.monument_type,
            );
            if (
                newMonumentSelected &&
                newMonumentSelected.id !== Number(stationMeta?.monument_type)
            ) {
                const monumentId = newMonumentSelected.id;
                getMonumentPhotoById(monumentId);
            } else if (
                newMonumentSelected &&
                newMonumentSelected.id === Number(stationMeta?.monument_type)
            ) {
                const monumentId = monumentType.find(
                    (mt) => mt.id === Number(stationMeta?.monument_type),
                )?.id;
                getMonumentPhotoById(monumentId);
            }
        }
    }, [stationMeta, monumentType, formState.stationMeta.monument_type]);

    const handleChange = (
        e:
            | React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
            | { target: { name: string; value: string } },
    ) => {
        const { value, name } = e.target;
        dispatch({
            type: "change_value",
            payload: {
                inputName: name,
                inputValue: value,
            },
        });

        //Conversion de lat, lon, heigth a x,y,z
        if (
            name === "station.lat" ||
            name === "station.lon" ||
            name === "station.height"
        ) {
            let lat =
                formState.station.lat === ""
                    ? NaN
                    : Number(formState.station.lat);
            let lon =
                formState.station.lon === ""
                    ? NaN
                    : Number(formState.station.lon);
            let height =
                formState.station.height === ""
                    ? NaN
                    : Number(formState.station.height);

            //Si alguno es vacio setealo con Nan
            switch (name) {
                case "station.lat":
                    lat = value !== "" ? Number(value) : NaN;
                    break;
                case "station.lon":
                    lon = value !== "" ? Number(value) : NaN;
                    break;
                case "station.height":
                    height = value !== "" ? Number(value) : NaN;
                    break;
            }

            const { x, y, z } = lla2ecef([lat, lon, height]);
            dispatch({
                type: "change_value",
                payload: {
                    inputName: "station.auto_x",
                    inputValue: x.toString(),
                },
            });
            dispatch({
                type: "change_value",
                payload: {
                    inputName: "station.auto_y",
                    inputValue: y.toString(),
                },
            });
            dispatch({
                type: "change_value",
                payload: {
                    inputName: "station.auto_z",
                    inputValue: z.toString(),
                },
            });
        }

        //Conversion de x,y,z a lat, lon, heigth
        if (
            name === "station.auto_x" ||
            name === "station.auto_y" ||
            name === "station.auto_z"
        ) {
            let x =
                formState.station.auto_x === ""
                    ? NaN
                    : Number(formState.station.auto_x);
            let y =
                formState.station.auto_y === ""
                    ? NaN
                    : Number(formState.station.auto_y);
            let z =
                formState.station.auto_z === ""
                    ? NaN
                    : Number(formState.station.auto_z);

            //Si alguno es vacio setealo con Nan
            switch (name) {
                case "station.auto_x":
                    x = value !== "" ? Number(value) : NaN;
                    break;
                case "station.auto_y":
                    y = value !== "" ? Number(value) : NaN;
                    break;
                case "station.auto_z":
                    z = value !== "" ? Number(value) : NaN;
                    break;
            }

            const { lat, lon, alt } = ecef2lla([x, y, z]);
            dispatch({
                type: "change_value",
                payload: {
                    inputName: "station.lat",
                    inputValue: lat.toString(),
                },
            });
            dispatch({
                type: "change_value",
                payload: {
                    inputName: "station.lon",
                    inputValue: lon.toString(),
                },
            });
            dispatch({
                type: "change_value",
                payload: {
                    inputName: "station.height",
                    inputValue: alt.toString(),
                },
            });
        }
    };

    const updateMetadata = async () => {
        try {
            if (station && stationMeta) {
                setUpdateLoading(true);

                const updatedRichText = classHtml(richText);

                const meta = {
                    has_battery: formState.booleans.has_battery,
                    has_communications: formState.booleans.has_communications,
                    comments: updatedRichText,
                    remote_access_link:
                        formState.stationMeta.remote_access_link,
                    battery_description:
                        formState.booleansDesc.battery_description,
                    communications_description:
                        formState.booleansDesc.communications_description,
                    station_type: stationType.find(
                        (st) => st.name === formState.stationMeta.station_type,
                    )?.id,
                    monument_type: monumentType.find(
                        (mt) => mt.name === formState.stationMeta.monument_type,
                    )?.id,
                    status: stationStatus.find(
                        (st) => st.name === formState.stationMeta.status,
                    )?.id,
                    navigation_file_delete: false,
                    station: stationId,
                };

                const formData = new FormData();

                Object.entries(meta).forEach(([key, value]) => {
                    if (value) {
                        formData.append(key, String(value));
                    }
                });

                const resMeta = await patchStationMetaService<
                    StationMetadataServiceData | ErrorResponse
                >(api, Number(station?.api_id), meta);
                if ("msg" in resMeta) {
                    setMetaMsg({
                        status: resMeta.statusCode,
                        msg: resMeta.response.type,
                        errors: resMeta.response,
                    });
                } else {
                    setMetaMsg({
                        status: Number(resMeta.statusCode),
                        msg: "Metadata updated successfully",
                    });
                }
                const stationParams = {
                    ...formState.station,
                    harpos_coeff_otl: formState.stationMeta.harpos_coeff_otl,
                    max_dist: formState.stationMeta.max_dist,
                    dome: formState.stationMeta.dome ?? "",
                    station_name: formState.stationMeta.station_name ?? "",
                    harpos_coeff_otl_by_file: oceanTideFile,
                };

                if (oceanTideType === "by file") {
                    delete (stationParams as any).harpos_coeff_otl;
                } else if (oceanTideType === "manual") {
                    delete (stationParams as any).harpos_coeff_otl_by_file;
                }

                const stationFormData = new FormData();

                Object.entries(stationParams).forEach(([key, value]) => {
                    if (value) {
                        stationFormData.append(key, String(value));
                    }
                });
                const res = await patchStationService<
                    ExtendedStationData | ErrorResponse
                >(api, Number(station?.api_id), stationParams);
                const errorRes = res as ErrorResponse;
                if (res.statusCode !== 200 && "status" in res) {
                    setStationMsg({
                        status: errorRes.statusCode,
                        msg: errorRes.msg,
                        errors: errorRes.response,
                    });
                } else if (res.statusCode === 200) {
                    setStationMsg({
                        status: Number(res.statusCode),
                        msg: "Station updated successfully",
                    });
                }

                if (resMeta.statusCode === 200 && res.statusCode === 200) {
                    Promise.all([getStationMeta(), getStation()]).then(() => {
                        setLoading(false);
                        setUpdateLoading(false);
                    });
                    setTimeout(() => {
                        setEdit(false);
                    }, 1000);
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setUpdateLoading(false);
        }
    };

    const generalFields = [
        "Station Type",
        "Monument",
        "Status",
        "Remote Access Link",
        "Station Name",
        "Domes Number",
        "Ocean Tide Loading Model",
        "Max distance",
    ];
    const generalFields2 = ["Battery", "Communications"];
    const generalFields3 = [
        "First rinex",
        "Last rinex",
        "Comments",
        "Navigation File",
    ];
    const equipmentFields = [
        "Antenna Code",
        "Antenna Serial",
        "Height Code",
        "Receiver Code",
        "Receiver Serial",
        "Receiver Version",
        "Radome Code",
    ];

    const inputsWithSelectKey = ["station_type", "monument_type", "status"];

    const inputRefType = useRef<HTMLInputElement>(null);

    const inputRefMonument = useRef<HTMLInputElement>(null);

    const inputRefStatus = useRef<HTMLInputElement>(null);

    const selectRef = (key: string) => {
        return key === "station_type"
            ? inputRefType
            : key === "monument_type"
              ? inputRefMonument
              : key === "status"
                ? inputRefStatus
                : null;
    };

    const handleGetFile = async (file: StationFilesData) => {
        const res = await getFileById(file.id);
        if (res) {
            const link = document.createElement("a");

            link.href = `data:application/octet-stream;base64,${res.actual_file}`;
            link.download = res.filename;
            link.click();
        }
    };

    const isPdf = (file: string) => {
        return file.includes(".pdf");
    };

    const setEditFile = (file: StationFilesData) => {
        setModals({ show: true, title: "AddFile", type: "edit" });
        setFileToEdit(file);
    };

    const otlErrorBadge = metaMsg?.errors?.errors?.find(
        (error) => error.attr === "harpos_coeff_otl",
    );

    return (
        <Modal
            close={close}
            modalId={"Metadata"}
            size={size}
            setModalState={setModalState}
            handleCloseModal={() => {
                refetch();
            }}
        >
            <div className="w-full inline-flex">
                <h3 className="font-bold text-center text-3xl my-2 grow">
                    Metadata
                </h3>
                <button
                    className="flex items-center btn btn-ghost btn-circle"
                    onClick={() => {
                        setEdit(!edit);
                    }}
                >
                    <PencilSquareIcon title="edit" className="size-8" />
                </button>
            </div>
            {loading ? (
                <LargeSkeleton />
            ) : (
                <div className="space-y-4">
                    <div className="grid grid-cols-2 space-y-4 grid-flow-dense">
                        <div className="card bg-base-200 grow shadow-xl mr-4">
                            <h2 className="card-title border-b-2 border-base-300 p-2">
                                General
                            </h2>

                            <div className="card-body">
                                <div className="grid grid-cols-2 gap-6">
                                    {Object.keys(formState.stationMeta).map(
                                        (key, idx) => {
                                            const keysToNotShow = [
                                                "harpos_coeff_otl",
                                            ];

                                            if (
                                                key &&
                                                !keysToNotShow.includes(key)
                                            ) {
                                                const errorBadge =
                                                    metaMsg?.errors?.errors?.find(
                                                        (error) =>
                                                            error.attr === key,
                                                    );
                                                const maxDistErrorBadge =
                                                    stationMsg?.errors?.errors?.find(
                                                        (error) =>
                                                            error.attr ===
                                                            "max_dist",
                                                    );

                                                return (
                                                    <div key={key}>
                                                        <div
                                                            className="text-sm font-bold flex items-center"
                                                            title={
                                                                generalFields[
                                                                    idx
                                                                ]
                                                            }
                                                        >
                                                            {generalFields[idx]}{" "}
                                                            <div
                                                                className={`size-3  rounded-full ml-3`}
                                                                title={
                                                                    generalFields[
                                                                        idx
                                                                    ]
                                                                }
                                                            ></div>
                                                        </div>
                                                        {edit ? (
                                                            <div className="flex flex-col space-y-1">
                                                                <label
                                                                    className={`input input-bordered flex items-center ${errorBadge || (key === "max_dist" && maxDistErrorBadge) ? "input-error" : ""}  `}
                                                                    title={
                                                                        errorBadge
                                                                            ? errorBadge.detail
                                                                            : key ===
                                                                                    "max_dist" &&
                                                                                maxDistErrorBadge
                                                                              ? maxDistErrorBadge.detail
                                                                              : ""
                                                                    }
                                                                    style={
                                                                        inputsWithSelectKey.includes(
                                                                            key,
                                                                        )
                                                                            ? {
                                                                                  padding:
                                                                                      "0",
                                                                              }
                                                                            : {}
                                                                    }
                                                                >
                                                                    {inputsWithSelectKey.includes(
                                                                        key,
                                                                    ) ? (
                                                                        <select
                                                                            className="select select-ghost w-full focus:outline-none focus:border-transparent focus:ring-0 focus:scale-95"
                                                                            name={
                                                                                "stationMeta." +
                                                                                key
                                                                            }
                                                                            value={
                                                                                formState
                                                                                    .stationMeta[
                                                                                    key as keyof typeof formState.stationMeta
                                                                                ] ??
                                                                                ""
                                                                            }
                                                                            style={{
                                                                                fontSize:
                                                                                    "16px",
                                                                                textOverflow:
                                                                                    "ellipsis",
                                                                                overflow:
                                                                                    "hidden",
                                                                                whiteSpace:
                                                                                    "nowrap",
                                                                            }}
                                                                            onChange={(
                                                                                e,
                                                                            ) => {
                                                                                dispatch(
                                                                                    {
                                                                                        type: "change_value",
                                                                                        payload:
                                                                                            {
                                                                                                inputName:
                                                                                                    e
                                                                                                        .target
                                                                                                        .name,
                                                                                                inputValue:
                                                                                                    e
                                                                                                        .target
                                                                                                        .value,
                                                                                            },
                                                                                    },
                                                                                );
                                                                            }}
                                                                        >
                                                                            <option
                                                                                value=""
                                                                                disabled
                                                                            >
                                                                                Select
                                                                                a{" "}
                                                                                {key.replace(
                                                                                    "_",
                                                                                    " ",
                                                                                )}
                                                                            </option>
                                                                            {(key ===
                                                                            "station_type"
                                                                                ? stationType
                                                                                : key ===
                                                                                    "monument_type"
                                                                                  ? monumentType
                                                                                  : key ===
                                                                                      "status"
                                                                                    ? stationStatus
                                                                                    : []
                                                                            ).map(
                                                                                (
                                                                                    item,
                                                                                ) => (
                                                                                    <option
                                                                                        className="truncate"
                                                                                        key={
                                                                                            item.id
                                                                                        }
                                                                                        value={
                                                                                            item.name
                                                                                        }
                                                                                        title={
                                                                                            item.name
                                                                                        }
                                                                                    >
                                                                                        {item
                                                                                            .name
                                                                                            .length >
                                                                                        30
                                                                                            ? item.name.slice(
                                                                                                  0,
                                                                                                  30,
                                                                                              ) +
                                                                                              "..."
                                                                                            : item.name}
                                                                                    </option>
                                                                                ),
                                                                            )}
                                                                        </select>
                                                                    ) : (
                                                                        <input
                                                                            className={
                                                                                "w-full "
                                                                            }
                                                                            autoComplete="off"
                                                                            type="text"
                                                                            ref={selectRef(
                                                                                key,
                                                                            )}
                                                                            value={
                                                                                formState
                                                                                    .stationMeta[
                                                                                    key as keyof typeof formState.stationMeta
                                                                                ] ??
                                                                                ""
                                                                            }
                                                                            name={
                                                                                "stationMeta." +
                                                                                key
                                                                            }
                                                                            onChange={(
                                                                                e,
                                                                            ) =>
                                                                                handleChange(
                                                                                    e,
                                                                                )
                                                                            }
                                                                        />
                                                                    )}

                                                                    {errorBadge ? (
                                                                        <span className="badge badge-error self-start -mt-2">
                                                                            {
                                                                                errorBadge.code
                                                                            }
                                                                        </span>
                                                                    ) : key ===
                                                                          "max_dist" &&
                                                                      maxDistErrorBadge ? (
                                                                        <span className="badge badge-error self-start -mt-2">
                                                                            {
                                                                                maxDistErrorBadge.code
                                                                            }
                                                                        </span>
                                                                    ) : null}
                                                                </label>
                                                            </div>
                                                        ) : key ===
                                                          "remote_access_link" ? (
                                                            formState
                                                                .stationMeta[
                                                                key as keyof typeof formState.stationMeta
                                                            ] ? (
                                                                <a
                                                                    target="_blank"
                                                                    className="link link-hover break-words"
                                                                    href={
                                                                        formState
                                                                            .stationMeta[
                                                                            key as keyof typeof formState.stationMeta
                                                                        ]
                                                                    }
                                                                >
                                                                    {
                                                                        formState
                                                                            .stationMeta[
                                                                            key as keyof typeof formState.stationMeta
                                                                        ]
                                                                    }
                                                                </a>
                                                            ) : (
                                                                <span className="text-gray-400">
                                                                    No info
                                                                </span>
                                                            )
                                                        ) : (
                                                            <p className="break-words whitespace-pre-wrap max-h-[150px] overflow-y-auto">
                                                                {formState
                                                                    .stationMeta[
                                                                    key as keyof typeof formState.stationMeta
                                                                ] &&
                                                                formState
                                                                    .stationMeta[
                                                                    key as keyof typeof formState.stationMeta
                                                                ] !== "" ? (
                                                                    formState
                                                                        .stationMeta[
                                                                        key as keyof typeof formState.stationMeta
                                                                    ]
                                                                ) : (
                                                                    <span className="text-gray-400">
                                                                        No info
                                                                    </span>
                                                                )}
                                                            </p>
                                                        )}
                                                    </div>
                                                );
                                            }
                                        },
                                    )}
                                    {Object.entries(formState.booleans).map(
                                        ([key, value], idx) => {
                                            if (key) {
                                                const pointer = value
                                                    ? "bg-green-500"
                                                    : "bg-red-500";

                                                const descKey =
                                                    key === "has_battery"
                                                        ? "battery_description"
                                                        : "communications_description";

                                                const errorBadge =
                                                    metaMsg?.errors?.errors?.find(
                                                        (error) =>
                                                            error.attr ===
                                                            descKey,
                                                    );

                                                return (
                                                    <div key={key}>
                                                        <div className="text-sm font-bold flex items-center">
                                                            {
                                                                generalFields2[
                                                                    idx
                                                                ]
                                                            }
                                                            {edit ? (
                                                                <input
                                                                    type="checkbox"
                                                                    className={`toggle ml-2`}
                                                                    style={{
                                                                        borderRadius:
                                                                            "50px",
                                                                        color: value
                                                                            ? "rgb(21 128 61)"
                                                                            : "rgb(185 28 28)",
                                                                    }}
                                                                    onChange={(
                                                                        e,
                                                                    ) => {
                                                                        dispatch(
                                                                            {
                                                                                type: "change_value",
                                                                                payload:
                                                                                    {
                                                                                        inputName:
                                                                                            "booleans." +
                                                                                            key,
                                                                                        inputValue:
                                                                                            e
                                                                                                .target
                                                                                                .checked,
                                                                                    },
                                                                            },
                                                                        );
                                                                    }}
                                                                    checked={
                                                                        formState
                                                                            .booleans[
                                                                            key as keyof typeof formState.booleans
                                                                        ]
                                                                    }
                                                                />
                                                            ) : (
                                                                <div
                                                                    className={`size-3 ${pointer} rounded-full ml-3`}
                                                                    title={
                                                                        generalFields2[
                                                                            idx
                                                                        ]
                                                                    }
                                                                ></div>
                                                            )}
                                                        </div>

                                                        {edit ? (
                                                            <div className="flex flex-col space-y-1">
                                                                <label
                                                                    className={`input input-bordered ${errorBadge ? "input-error" : ""} flex items-center`}
                                                                >
                                                                    <input
                                                                        className="w-full"
                                                                        autoComplete="off"
                                                                        type="text"
                                                                        value={
                                                                            formState
                                                                                .booleansDesc[
                                                                                descKey
                                                                            ]
                                                                        }
                                                                        name={
                                                                            "booleansDesc." +
                                                                            descKey
                                                                        }
                                                                        onChange={(
                                                                            e,
                                                                        ) =>
                                                                            handleChange(
                                                                                e,
                                                                            )
                                                                        }
                                                                    />
                                                                </label>
                                                                {errorBadge && (
                                                                    <span className="badge badge-error self-end">
                                                                        {
                                                                            errorBadge.code
                                                                        }
                                                                    </span>
                                                                )}
                                                            </div>
                                                        ) : (
                                                            <p className="break-words">
                                                                {formState
                                                                    .booleansDesc[
                                                                    descKey
                                                                ] !== "" ? (
                                                                    formState
                                                                        .booleansDesc[
                                                                        descKey
                                                                    ]
                                                                ) : (
                                                                    <span className="text-gray-400">
                                                                        No
                                                                        Description
                                                                    </span>
                                                                )}
                                                            </p>
                                                        )}
                                                    </div>
                                                );
                                            }
                                        },
                                    )}
                                    {Object.entries(formState.rinex).map(
                                        ([key, value], idx) => {
                                            if (
                                                key !== "comments" &&
                                                key !== "navigation_file"
                                            ) {
                                                return (
                                                    <div key={key}>
                                                        <div className="text-sm font-bold flex items-center">
                                                            {
                                                                generalFields3[
                                                                    idx
                                                                ]
                                                            }
                                                        </div>

                                                        <p className="break-words">
                                                            {value ? (
                                                                formattedDates(
                                                                    new Date(
                                                                        value,
                                                                    ),
                                                                )
                                                            ) : (
                                                                <span className="text-gray-400">
                                                                    No date
                                                                </span>
                                                            )}
                                                        </p>
                                                    </div>
                                                );
                                            } else {
                                                // const errorBadge =
                                                //     metaMsg?.errors?.errors?.find(
                                                //         (error) =>
                                                //             error.attr === key,
                                                //     );

                                                // TODO: HANDLEAR EL APPLICATION, ESTA PUESTO SOLO PDF. XQ NOSE

                                                if (key === "navigation_file") {
                                                    return (
                                                        <div>
                                                            <div className="text-sm font-bold flex items-center justify-between">
                                                                Navigation File
                                                                {edit && (
                                                                    <button
                                                                        className="btn btn-ghost btn-circle ml-2 -mt-2"
                                                                        onClick={() => {
                                                                            setModals(
                                                                                {
                                                                                    show: true,
                                                                                    title: "AddFile",
                                                                                    type: "add",
                                                                                },
                                                                            );
                                                                            setFileType(
                                                                                "meta",
                                                                            );
                                                                        }}
                                                                        disabled={
                                                                            formState
                                                                                .rinex
                                                                                .navigation_file !==
                                                                            ""
                                                                        }
                                                                    >
                                                                        <PlusCircleIcon
                                                                            strokeWidth={
                                                                                1.5
                                                                            }
                                                                            stroke="currentColor"
                                                                            className="size-6"
                                                                        />
                                                                    </button>
                                                                )}
                                                            </div>

                                                            {edit ? (
                                                                <div className="flex flex-col space-y-1">
                                                                    <div className="bg-neutral-content p-4 rounded-md flex-grow flex items-center">
                                                                        {formState
                                                                            .rinex[
                                                                            key as keyof typeof formState.rinex
                                                                        ] && (
                                                                            <button
                                                                                className="btn btn-ghost btn-circle mr-4"
                                                                                onClick={() => {
                                                                                    setModals(
                                                                                        {
                                                                                            show: true,
                                                                                            title: "ConfirmDelete",
                                                                                            type: "edit",
                                                                                        },
                                                                                    );
                                                                                    setFileType(
                                                                                        "meta",
                                                                                    );
                                                                                }}
                                                                            >
                                                                                <TrashIcon className="size-6 text-red-600" />
                                                                            </button>
                                                                        )}
                                                                        <p className="break-words">
                                                                            {formState
                                                                                .rinex[
                                                                                key as keyof typeof formState.rinex
                                                                            ] ? (
                                                                                formState
                                                                                    .rinex[
                                                                                    key as keyof typeof formState.rinex
                                                                                ]
                                                                            ) : (
                                                                                <span className="text-gray-400">
                                                                                    No
                                                                                    info
                                                                                </span>
                                                                            )}
                                                                        </p>
                                                                        {formState
                                                                            .rinex[
                                                                            key as keyof typeof formState.rinex
                                                                        ] && (
                                                                            <a
                                                                                className="btn-circle btn-ghost flex justify-center w-4/12"
                                                                                download={
                                                                                    formState
                                                                                        .rinex
                                                                                        .navigation_file
                                                                                }
                                                                                href={`data:application/octet-stream;base64,${stationMeta?.navigation_actual_file}`}
                                                                            >
                                                                                <ArrowDownTrayIcon className="size-6 self-center" />
                                                                            </a>
                                                                        )}
                                                                    </div>
                                                                </div>
                                                            ) : (
                                                                <p className="break-words">
                                                                    {formState
                                                                        .rinex[
                                                                        key as keyof typeof formState.rinex
                                                                    ] &&
                                                                    formState
                                                                        .rinex[
                                                                        key as keyof typeof formState.rinex
                                                                    ] !== "" ? (
                                                                        formState
                                                                            .rinex[
                                                                            key as keyof typeof formState.rinex
                                                                        ]
                                                                    ) : (
                                                                        <span className="text-gray-400">
                                                                            No
                                                                            info
                                                                        </span>
                                                                    )}
                                                                </p>
                                                            )}
                                                        </div>
                                                    );
                                                }
                                            }
                                        },
                                    )}
                                </div>
                            </div>
                        </div>

                        <div className="flex flex-col items-center">
                            <h3 className="font-bold text-xl my-2">
                                Monument Photo
                            </h3>
                            <img
                                className="size-96 object-contain"
                                src={
                                    chosenMonumentPhoto
                                        ? "data:image/png;base64," +
                                          chosenMonumentPhoto
                                        : defPhoto
                                }
                                alt={
                                    monumentType.find(
                                        (mt) =>
                                            mt.id ===
                                            Number(stationMeta?.monument_type),
                                    )?.name
                                }
                            />
                        </div>
                    </div>
                    <div className="grid grid-cols-1 space-y-4 grid-flow-dense">
                        <div className="card bg-base-200 grow shadow-xl">
                            <h2 className="card-title border-b-2 border-base-300 p-2 justify-between">
                                Comments
                            </h2>
                            <div className="max-h-48 h-auto">
                                {edit ? (
                                    <QuillText
                                        value={
                                            metaMsg?.errors
                                                ? formattedData.rinex.comments
                                                : richText
                                        }
                                        setValue={setRichText}
                                        clase="h-48 pb-12"
                                    />
                                ) : formattedData.rinex.comments ? (
                                    <div
                                        className="textarea-bordered rounded-md overflow-auto p-4 max-h-48"
                                        dangerouslySetInnerHTML={{
                                            __html:
                                                formattedData.rinex.comments ??
                                                "",
                                        }}
                                    />
                                ) : (
                                    <div className="text-center text-neutral text-2xl font-bold w-full rounded-md bg-neutral-content p-6">
                                        There are no comments registered
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="grid grid-cols-2 space-x-4 grid-flow-dense">
                        <div className="card bg-base-200 grow shadow-xl">
                            <h2 className="card-title border-b-2 border-base-300 p-2 justify-between relative">
                                Attached Files
                                {edit && (
                                    <button
                                        className="btn btn-ghost btn-circle ml-2 absolute right-3"
                                        onClick={() => {
                                            setModals({
                                                show: true,
                                                title: "AddFile",
                                                type: "add",
                                            });
                                        }}
                                    >
                                        <PlusCircleIcon
                                            strokeWidth={1.5}
                                            stroke="currentColor"
                                            className="w-8 h-10"
                                        />
                                    </button>
                                )}
                            </h2>
                            <div
                                className={`card-body ${showAllFiles ? "overflow-y-auto max-h-44 scrollbar-base" : ""}`}
                            >
                                <div
                                    className={`grid ${files && files.length > 0 ? "grid-cols-2 md:grid-cols-2" : "grid-cols-1"} grid-flow-dense gap-2`}
                                >
                                    {files && files.length > 0 ? (
                                        files
                                            .slice(
                                                0,
                                                showAllFiles ? files.length : 2,
                                            )
                                            .map((file) => {
                                                return (
                                                    <div
                                                        className="flex items-center w-full rounded-md bg-neutral-content"
                                                        key={
                                                            file.filename +
                                                            file.id
                                                        }
                                                    >
                                                        <div className="flex-grow overflow-hidden ">
                                                            <div className="p-6 flex w-full justify-between items-center">
                                                                {edit && (
                                                                    <button
                                                                        className="btn btn-ghost btn-circle mr-4"
                                                                        onClick={() => {
                                                                            setModals(
                                                                                {
                                                                                    show: true,
                                                                                    title: "ConfirmDelete",
                                                                                    type: "edit",
                                                                                },
                                                                            );
                                                                            setFileToDel(
                                                                                file.id,
                                                                            );
                                                                        }}
                                                                    >
                                                                        <TrashIcon className="size-8 text-red-600" />
                                                                    </button>
                                                                )}
                                                                <div className="flex flex-col w-8/12 text-wrap truncate max-w-full">
                                                                    <h2
                                                                        className="font-semibold text-xl mb-2 truncate"
                                                                        title={
                                                                            file.filename
                                                                        }
                                                                    >
                                                                        {
                                                                            file.filename
                                                                        }
                                                                    </h2>
                                                                    <p
                                                                        className="truncate"
                                                                        title={
                                                                            file.description
                                                                        }
                                                                    >
                                                                        {
                                                                            file.description
                                                                        }
                                                                    </p>
                                                                </div>
                                                                <a
                                                                    className="btn-circle btn-ghost cursor-pointer flex justify-center w-4/12"
                                                                    onClick={async () => {
                                                                        edit
                                                                            ? setEditFile(
                                                                                  file,
                                                                              )
                                                                            : isPdf(
                                                                                    file.filename,
                                                                                )
                                                                              ? setFileToShow(
                                                                                    await getFileById(
                                                                                        file.id,
                                                                                    ),
                                                                                )
                                                                              : handleGetFile(
                                                                                    file,
                                                                                );
                                                                    }}
                                                                >
                                                                    {edit ? (
                                                                        <svg
                                                                            xmlns="http://www.w3.org/2000/svg"
                                                                            fill="none"
                                                                            viewBox="0 0 24 24"
                                                                            strokeWidth={
                                                                                1.5
                                                                            }
                                                                            stroke="currentColor"
                                                                            className="size-6 self-center"
                                                                        >
                                                                            <path
                                                                                strokeLinecap="round"
                                                                                strokeLinejoin="round"
                                                                                d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10"
                                                                            />
                                                                        </svg>
                                                                    ) : isPdf(
                                                                          file.filename,
                                                                      ) ? (
                                                                        <BookOpenIcon className="size-6 self-center" />
                                                                    ) : (
                                                                        <ArrowDownTrayIcon className="size-6 self-center" />
                                                                    )}
                                                                </a>
                                                            </div>
                                                        </div>
                                                    </div>
                                                );
                                            })
                                    ) : (
                                        <div className="text-center text-neutral text-2xl font-bold w-full rounded-md bg-neutral-content p-6">
                                            There are no files registered
                                        </div>
                                    )}
                                </div>
                            </div>
                            {files && files.length > 2 && (
                                <div className="text-center my-4 font-bold">
                                    {!showAllFiles ? (
                                        <button onClick={handleShowMore}>
                                            Show More
                                        </button>
                                    ) : (
                                        <button onClick={handleShowLess}>
                                            Show Less
                                        </button>
                                    )}
                                </div>
                            )}
                        </div>
                        <div className="card bg-base-200 grow shadow-xl">
                            <h2 className="card-title border-b-2 border-base-300 p-2 justify-between">
                                Ocean Tide Loading Model
                            </h2>
                            <div className="max-h-48 overflow-y-auto h-full">
                                {edit ? (
                                    <>
                                        <div className="flex gap-2 mb-2">
                                            <button
                                                className={`btn flex-1 ${oceanTideType === "by file" ? "btn-primary" : ""}`}
                                                onClick={() =>
                                                    setOceanTideType("by file")
                                                }
                                            >
                                                By File
                                            </button>
                                            <button
                                                className={`btn flex-1 ${oceanTideType === "manual" ? "btn-primary" : ""}`}
                                                onClick={() =>
                                                    setOceanTideType("manual")
                                                }
                                            >
                                                Manual
                                            </button>
                                        </div>
                                        {oceanTideType === "manual" ? (
                                            <textarea
                                                className={
                                                    "textarea h-44 w-full textarea-ghost resize-none"
                                                }
                                                autoComplete="off"
                                                value={
                                                    formState.stationMeta[
                                                        "harpos_coeff_otl" as keyof typeof formState.stationMeta
                                                    ] ?? ""
                                                }
                                                name={
                                                    "stationMeta." +
                                                    "harpos_coeff_otl"
                                                }
                                                onChange={(e) =>
                                                    handleChange(e)
                                                }
                                            ></textarea>
                                        ) : (
                                            oceanTideType === "by file" && (
                                                <div className="">
                                                    <Dropzone
                                                        setFile={
                                                            setOceanTideFile
                                                        }
                                                        file={oceanTideFile}
                                                    />
                                                </div>
                                            )
                                        )}
                                        {otlErrorBadge && (
                                            <span className="badge badge-error self-start -mt-2">
                                                {otlErrorBadge.code}
                                            </span>
                                        )}
                                    </>
                                ) : (
                                    <>
                                        {formState.stationMeta[
                                            "harpos_coeff_otl" as keyof typeof formState.stationMeta
                                        ] &&
                                        formState.stationMeta[
                                            "harpos_coeff_otl" as keyof typeof formState.stationMeta
                                        ] !== "" ? (
                                            <p className="break-words whitespace-pre-wrap overflow-y-auto h-full p-2">
                                                {
                                                    formState.stationMeta[
                                                        "harpos_coeff_otl" as keyof typeof formState.stationMeta
                                                    ]
                                                }
                                            </p>
                                        ) : (
                                            <div className="card-body">
                                                <div className="text-center text-neutral text-2xl font-bold w-full rounded-md bg-neutral-content p-6">
                                                    There is no ocean tide
                                                    loading model
                                                </div>
                                            </div>
                                        )}
                                    </>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="grid grid-cols-2 space-x-4 grid-flow-dense">
                        <div className="card bg-base-200 grow shadow-xl">
                            <h2 className="card-title border-b-2 border-base-300 p-2 justify-between">
                                Geodetic Coordinates
                                <button
                                    className={` ${showPopup && copyId === "geodetic" ? "tooltip tooltip-open" : ""} mr-2`}
                                    data-tip="Copied !"
                                >
                                    <ClipboardDocumentIcon
                                        className="size-6 cursor-pointer rounded-md transition-all duration-75 btn-ghost hover:scale-125"
                                        title={"copy coordinates"}
                                        onClick={() => {
                                            navigator.clipboard.writeText(
                                                "LATITUDE: " +
                                                    formState.station.lat +
                                                    ",LONGITUDE: " +
                                                    formState.station.lon +
                                                    ",HEIGHT: " +
                                                    formState.station.height,
                                            );
                                            setCopyId("geodetic");
                                            show();
                                        }}
                                    />
                                </button>
                            </h2>
                            <div className="card-body">
                                <div className="grid grid-cols-3 gap-2">
                                    {["lat", "lon", "height"].map(
                                        (key, idx) => {
                                            if (key) {
                                                const latLon = ["lat", "lon"];

                                                const errorBadge =
                                                    stationMsg?.errors?.errors?.find(
                                                        (error) =>
                                                            error.attr === key,
                                                    );

                                                return (
                                                    <div
                                                        key={`geodetic-${key}-${idx}`}
                                                    >
                                                        <div className="text-sm font-bold flex items-center">
                                                            {key === "lat"
                                                                ? "Latitude"
                                                                : key === "lon"
                                                                  ? "Longitude"
                                                                  : "Height"}
                                                        </div>
                                                        {edit ? (
                                                            <div className="flex flex-col space-y-1">
                                                                <label
                                                                    className={`input input-bordered ${errorBadge ? "input-error" : ""} flex items-center`}
                                                                    title={
                                                                        errorBadge
                                                                            ? errorBadge.detail
                                                                            : ""
                                                                    }
                                                                >
                                                                    <input
                                                                        className="w-full"
                                                                        autoComplete="off"
                                                                        type="text"
                                                                        value={
                                                                            formState
                                                                                .station[
                                                                                key as keyof typeof formState.station
                                                                            ] ??
                                                                            ""
                                                                        }
                                                                        title={
                                                                            errorBadge
                                                                                ? errorBadge.detail
                                                                                : ""
                                                                        }
                                                                        name={
                                                                            "station." +
                                                                            key
                                                                        }
                                                                        onChange={(
                                                                            e,
                                                                        ) =>
                                                                            handleChange(
                                                                                e,
                                                                            )
                                                                        }
                                                                    />
                                                                </label>
                                                                {errorBadge && (
                                                                    <span className="badge badge-error self-end">
                                                                        {
                                                                            errorBadge.code
                                                                        }
                                                                    </span>
                                                                )}
                                                            </div>
                                                        ) : (
                                                            <p className="break-all">
                                                                {formState
                                                                    .station[
                                                                    key as keyof typeof formState.station
                                                                ] ? (
                                                                    latLon.includes(
                                                                        key,
                                                                    ) ? (
                                                                        decimalToDMS(
                                                                            Number(
                                                                                formState
                                                                                    .station[
                                                                                    key as keyof typeof formState.station
                                                                                ],
                                                                            ),
                                                                            key ===
                                                                                "lat",
                                                                        )
                                                                    ) : (
                                                                        Number(
                                                                            formState
                                                                                .station[
                                                                                key as keyof typeof formState.station
                                                                            ],
                                                                        ) + " m"
                                                                    )
                                                                ) : (
                                                                    <span className="text-gray-400">
                                                                        No {key}
                                                                    </span>
                                                                )}
                                                            </p>
                                                        )}
                                                    </div>
                                                );
                                            }
                                        },
                                    )}
                                </div>
                            </div>
                        </div>
                        <div className="card bg-base-200 grow shadow-xl">
                            <h2 className="card-title border-b-2 border-base-300 p-2 justify-between">
                                Cartesian Coordinates
                                <button
                                    className={` ${showPopup && copyId === "coordinates" ? "tooltip tooltip-open" : ""} mr-2`}
                                    data-tip="Copied !"
                                >
                                    <ClipboardDocumentIcon
                                        className="size-6 cursor-pointer rounded-md transition-all duration-75 btn-ghost hover:scale-125"
                                        title={"copy coordinates"}
                                        onClick={() => {
                                            navigator.clipboard.writeText(
                                                "X: " +
                                                    formState.station.auto_x +
                                                    ",Y: " +
                                                    formState.station.auto_y +
                                                    ",Z: " +
                                                    formState.station.auto_z,
                                            );
                                            setCopyId("coordinates");
                                            show();
                                        }}
                                    />
                                </button>
                            </h2>
                            <div className="card-body">
                                <div className="grid grid-cols-3 md:grid-cols-2 grid-flow-dense gap-2">
                                    {["auto_x", "auto_y", "auto_z"].map(
                                        (key, idx) => {
                                            if (key) {
                                                const errorBadge =
                                                    stationMsg?.errors?.errors?.find(
                                                        (error) =>
                                                            error.attr === key,
                                                    );

                                                return (
                                                    <div
                                                        key={`cartesian-${key}-${idx}`}
                                                    >
                                                        <div className="text-sm font-bold flex items-center">
                                                            {key === "auto_x"
                                                                ? "X"
                                                                : key ===
                                                                    "auto_y"
                                                                  ? "Y"
                                                                  : "Z"}
                                                        </div>
                                                        {edit ? (
                                                            <div className="flex flex-col space-y-1">
                                                                <label
                                                                    className={`input input-bordered ${errorBadge ? "input-error" : ""} flex items-center`}
                                                                >
                                                                    <input
                                                                        className="w-full"
                                                                        autoComplete="off"
                                                                        type="text"
                                                                        value={
                                                                            formState
                                                                                .station[
                                                                                key as keyof typeof formState.station
                                                                            ] ??
                                                                            ""
                                                                        }
                                                                        title={
                                                                            errorBadge
                                                                                ? errorBadge.detail
                                                                                : ""
                                                                        }
                                                                        name={
                                                                            "station." +
                                                                            key
                                                                        }
                                                                        onChange={(
                                                                            e,
                                                                        ) =>
                                                                            handleChange(
                                                                                e,
                                                                            )
                                                                        }
                                                                    />
                                                                </label>
                                                                {errorBadge && (
                                                                    <span className="badge badge-error self-end">
                                                                        {
                                                                            errorBadge.code
                                                                        }
                                                                    </span>
                                                                )}
                                                            </div>
                                                        ) : (
                                                            <p className="break-words">
                                                                {formState
                                                                    .station[
                                                                    key as keyof typeof formState.station
                                                                ] ? (
                                                                    Number(
                                                                        formState
                                                                            .station[
                                                                            key as keyof typeof formState.station
                                                                        ],
                                                                    ) + " m"
                                                                ) : (
                                                                    <span className="text-gray-400">
                                                                        No {key}
                                                                    </span>
                                                                )}
                                                            </p>
                                                        )}
                                                    </div>
                                                );
                                            }
                                        },
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                    <div className="grid grid-cols-1 space-y-4 grid-flow-dense">
                        <div className="card bg-base-200 grow shadow-xl">
                            <h2 className="card-title border-b-2 border-base-300 p-2 justify-between">
                                Equipment
                            </h2>
                            <div className="card-body">
                                <div className="grid grid-cols-4 md:grid-cols-2 grid-flow-dense gap-2">
                                    {Object.entries(formState.equipment).map(
                                        ([key, value], idx) => {
                                            if (key) {
                                                return (
                                                    <div
                                                        key={`equipment-${key}-${idx}`}
                                                    >
                                                        <div className="text-sm font-bold flex items-center">
                                                            {
                                                                equipmentFields[
                                                                    idx
                                                                ]
                                                            }
                                                        </div>

                                                        <p className="break-words">
                                                            {value ? (
                                                                value
                                                            ) : (
                                                                <span className="text-gray-400">
                                                                    No{" "}
                                                                    {key.replace(
                                                                        "_",
                                                                        " ",
                                                                    )}
                                                                </span>
                                                            )}
                                                        </p>
                                                    </div>
                                                );
                                            }
                                        },
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            )}
            {edit && (
                <div className="w-full flex flex-col mt-4 items-center justify-center">
                    <Alert
                        msg={
                            metaMsg?.status === 200 &&
                            stationMsg?.status === 200
                                ? metaMsg
                                : metaMsg?.status !== 200
                                  ? metaMsg
                                  : stationMsg?.status !== 200
                                    ? stationMsg
                                    : undefined
                        }
                    />
                    <button
                        className="btn btn-success w-[140px] mt-4"
                        onClick={() => updateMetadata()}
                        disabled={loading || updateLoading}
                    >
                        {updateLoading && (
                            <div
                                className="inline-block size-6
                                mx-2 animate-spin rounded-full border-4 border-solid border-current border-e-transparent align-[-0.125em] text-white motion-reduce:animate-[spin_1.5s_linear_infinite]"
                                role="status"
                            ></div>
                        )}
                        UPDATE
                    </button>
                </div>
            )}

            {modals && modals?.title === "AddFile" && (
                <StationAddFileModal
                    stationId={stationId}
                    stationMetaId={stationMeta?.station}
                    meta={fileType === "meta"}
                    refetchStationMeta={() => {
                        Promise.all([getStationMeta(), getStation()]).then(
                            () => {
                                setLoading(false);
                                setUpdateLoading(false);
                            },
                        );
                        // refetchStationMeta && refetchStationMeta();
                        setFileType("none");
                    }}
                    reFetch={() => {
                        getFiles();
                        setLoading(false);
                        setFileType("none");
                    }}
                    setStateModal={setModals}
                    type={modals.type}
                    file={edit ? fileToEdit : undefined}
                    setFile={setFileToEdit}
                />
            )}

            {modals && modals.title === "FileRender" && (
                <RenderFileModal
                    file={`data:application/pdf;base64,${fileToShow?.actual_file}`}
                    filename={fileToShow?.filename}
                    closeModal={() => undefined}
                    setStateModal={setModals}
                />
            )}

            {modals && modals?.title === "ConfirmDelete" && (
                <ConfirmDeleteModal
                    msg={fileMsg}
                    loading={loading}
                    confirmRemove={() =>
                        fileType === "meta"
                            ? delFileMeta()
                            : delFile(fileToDel as number)
                    }
                    closeModal={() => {
                        setModals({
                            show: false,
                            title: "",
                            type: "edit",
                        });
                        setFileType("none");

                        setFileMsg(undefined);
                    }}
                />
            )}
        </Modal>
    );
};

export default StationMetadataModal;

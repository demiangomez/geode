import { useEffect, useState } from "react";
import {
    AddFileModal,
    Alert,
    ConfirmDeleteModal,
    ImageModal,
    Modal,
    RenderFileModal,
    Spinner,
    StationPeopleModal,
    VisitCampaignModal,
    VisitPeopleModal,
} from "@componentsReact";

// ------------------------ //
import Slider from "react-slick";
import "slick-carousel/slick/slick.css";
import "slick-carousel/slick/slick-theme.css";
import "@assets/slickCustom.css";
// ------------------------ //

import defPhoto from "@assets/images/placeholder.png";

import {
    ArrowDownTrayIcon,
    BookOpenIcon,
    PencilSquareIcon,
    PlusCircleIcon,
    TrashIcon,
    UserPlusIcon,
} from "@heroicons/react/24/outline";

import {
    delStationVisitFilesService,
    delStationVisitGnssFilesService,
    delStationVisitsImagesService,
    getPeopleService,
    getStationVisitFileByIdService,
    getStationVisitFilesService,
    getStationVisitGnssFileByIdService,
    getStationVisitGnssFilesService,
    getStationVisitsByIdService,
    getStationVisitsImagesService,
    patchStationVisitService,
} from "@services";

import { useFormReducer, useWaitCursor, useApi, useAuth } from "@hooks";

import { apiOkStatuses, classHtml, showModal } from "@utils";

import {
    People,
    PeopleServiceData,
    StationVisitsData,
    StationVisitsFilesData,
    StationVisitsFilesServiceData,
    Errors,
    ErrorResponse,
    StationCampaignsData,
} from "@types";
import QuillText from "@components/map/QuillText";

interface Props {
    campaigns: StationCampaignsData[] | undefined;
    visitId: number | undefined;
    closeModal: () => void;
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
}

type Photo = {
    id: number;
    actual_image: string;
    description: string;
    name: string;
};

type expandedStationVisitData = StationVisitsData & {
    statusCode: number;
};

const StationVisitDetailModal = ({
    campaigns,
    visitId,
    closeModal,
    setStateModal,
}: Props) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [fileMsg, setFileMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [peopleMsg, setPeopleMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [commentsMsg, setCommentsMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    // Visit photo

    const [, setBlurPhoto] = useState<
        { blur: boolean; id: number } | undefined
    >(undefined);

    const [delPhoto, setDelPhoto] = useState<
        StationVisitsFilesData | undefined
    >(undefined);

    const [photo, setPhoto] = useState<Photo | undefined>(undefined);

    // ---

    const [loading, setLoading] = useState<boolean>(false);
    const [imagesLoading, setImagesLoading] = useState<boolean>(false);
    const [commentLoading, setCommentLoading] = useState<boolean>(false);
    const [fileLoading, setFileLoading] = useState<boolean>(false);

    useWaitCursor(fileLoading);

    const [edit, setEdit] = useState<boolean>(false);

    const [visit, setVisit] = useState<StationVisitsData | undefined>(
        undefined,
    );

    const [images, setImages] = useState<StationVisitsFilesData[] | undefined>(
        undefined,
    );

    const [people, setPeople] = useState<People[]>([]);

    const [person, setPerson] = useState<People | undefined>(undefined);

    const [files, setFiles] = useState<StationVisitsFilesData[] | undefined>(
        undefined,
    );

    const [gnssFiles, setGnssFiles] = useState<
        StationVisitsFilesData[] | undefined
    >(undefined);

    const [richText, setRichText] = useState<string>("");

    const [fileType, setFileType] = useState<string | undefined>(undefined);

    const [fileToDel, setFileToDel] = useState<number | undefined>(undefined);

    const [personToDel, setPersonToDel] = useState<number | undefined>(
        undefined,
    );

    const [filteredObservationFiles, setFilteredObservationFiles] = useState<
        StationVisitsFilesData[] | undefined
    >(undefined);

    const [filteredOtherFiles, setFilteredOtherFiles] = useState<
        StationVisitsFilesData[] | undefined
    >(undefined);

    const [showGnssFiles, setShowGnssFiles] = useState<boolean>(false);

    const [showFiles, setShowFiles] = useState<boolean>(false);

    const [fileToEdit, setFileToEdit] = useState<
        StationVisitsFilesData | undefined
    >(undefined);

    const handleShowMore = (key: string) => {
        if (key === "gnss") {
            setShowGnssFiles(true);
        } else {
            setShowFiles(true);
        }
    };

    const handleShowLess = (key: string) => {
        if (key === "gnss") {
            setShowGnssFiles(false);
        } else {
            setShowFiles(false);
        }
    };

    const visitCampaign = campaigns?.find(
        (c) => c.id === Number(visit?.campaign),
    );

    const { formState, dispatch } = useFormReducer({
        comments: "",
        date: "",
    });

    const getVisitById = async () => {
        try {
            if (!visitId) return null;
            setLoading(true);
            const res =
                await getStationVisitsByIdService<expandedStationVisitData>(
                    api,
                    visitId,
                );
            if (res.statusCode === 200) {
                setVisit(res);
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    const getVisitImagesById = async () => {
        try {
            if (!visitId) return null;
            setImagesLoading(true);
            const res =
                await getStationVisitsImagesService<StationVisitsFilesServiceData>(
                    api,
                    {
                        limit: 0,
                        offset: 0,
                        visit_api_id: String(visitId),
                        thumbnail: true,
                    },
                );
            if (res.statusCode === 200) {
                setImages(res.data);
            }
        } catch (error) {
            console.error(error);
        } finally {
            setImagesLoading(false);
        }
    };

    const getVisitsAttachedFiles = async () => {
        try {
            if (!visitId) return null;
            setLoading(true);
            const res =
                await getStationVisitFilesService<StationVisitsFilesServiceData>(
                    api,
                    {
                        limit: 0,
                        offset: 0,
                        visit_api_id: String(visitId),
                        only_metadata: true,
                    },
                );

            if (res.statusCode === 200) {
                setFiles(res.data);
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    const getVisitAttachedFileById = async (id: number) => {
        try {
            setFileLoading(true);
            if (!visitId) return null;
            const res =
                await getStationVisitFileByIdService<StationVisitsFilesData>(
                    api,
                    id,
                );
            return res;
        } catch (error) {
            console.error(error);
        } finally {
            setFileLoading(false);
        }
    };

    const handleClearObservationFilter = (
        e: React.MouseEvent<HTMLButtonElement>,
    ) => {
        e.preventDefault();

        const previousSibling = e.currentTarget
            .previousElementSibling as HTMLInputElement | null;
        if (previousSibling) {
            previousSibling.value = "";
        }

        setFilteredObservationFiles(gnssFiles);
    };

    const handleClearOtherFilter = (e: React.MouseEvent<HTMLButtonElement>) => {
        e.preventDefault();

        const previousSibling = e.currentTarget
            .previousElementSibling as HTMLInputElement | null;
        if (previousSibling) {
            previousSibling.value = "";
        }

        setFilteredOtherFiles(files);
    };

    const delVisitAttachedFile = async () => {
        try {
            if (!fileToDel) return null;

            setLoading(true);
            const res = await delStationVisitFilesService<ErrorResponse>(
                api,
                fileToDel,
            );
            if ("status" in res && res.status === "success") {
                setFileMsg({
                    status: res.statusCode,
                    msg: res.msg,
                });
            } else {
                setFileMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const getVisitsGnssFiles = async () => {
        try {
            if (!visitId) return null;
            setLoading(true);
            const res =
                await getStationVisitGnssFilesService<StationVisitsFilesServiceData>(
                    api,
                    {
                        limit: 0,
                        offset: 0,
                        visit_api_id: String(visitId),
                        only_metadata: true,
                    },
                );
            if (res.statusCode === 200) {
                setGnssFiles(res.data);
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    const getVisitGnssFileById = async (id: number) => {
        try {
            setFileLoading(true);
            if (!visitId) return null;
            const res =
                await getStationVisitGnssFileByIdService<StationVisitsFilesData>(
                    api,
                    id ?? 0,
                );
            return res;
        } catch (error) {
            console.error(error);
        } finally {
            setFileLoading(false);
        }
    };

    const delVisitGnssFile = async () => {
        try {
            if (!fileToDel) return null;

            setLoading(true);
            const res = await delStationVisitGnssFilesService<ErrorResponse>(
                api,
                fileToDel,
            );
            if ("status" in res && res.status === "success") {
                setFileMsg({
                    status: res.statusCode,
                    msg: res.msg,
                });
            } else {
                setFileMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const getPeople = async () => {
        try {
            const res = await getPeopleService<PeopleServiceData>(api);
            setPeople(res.data);
        } catch (err) {
            console.error(err);
        }
    };

    const patchVisit = async () => {
        try {
            if (!visitId) return null;
            setCommentLoading(true);

            const rest = { ...visit };

            const updatedRichText = classHtml(richText);

            rest.comments = updatedRichText;
            rest.date = formState["date"];

            delete rest.log_sheet_actual_file;
            delete rest.log_sheet_filename;
            delete rest.navigation_actual_file;
            delete rest.navigation_filename;

            const formData = new FormData();

            Object.entries(rest).forEach(([key, value]) => {
                if (key.includes("campaign")) {
                    return;
                }

                if (key === "people") {
                    value.forEach((p: { id: number; name: string }) => {
                        formData.append("people", String(p.id));
                    });
                } else {
                    formData.append(key, value);
                }
            });

            const res = await patchStationVisitService<ErrorResponse>(
                api,
                visitId,
                formData,
            );
            if ("status" in res) {
                setCommentsMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            } else {
                setCommentsMsg({
                    status: 200,
                    msg: "Comments or Date updated successfully",
                });
                getAll();
            }
        } catch (err) {
            console.error(err);
        } finally {
            setCommentLoading(false);
            setTimeout(() => {
                setCommentsMsg(undefined);
            }, 2500);
            setEdit(false);
        }
    };

    const delPeople = async () => {
        try {
            if (!visitId) return null;
            setLoading(true);

            const peopleToDel = visit?.people.find(
                (p: { id: number }) => p.id === personToDel,
            );

            if (!peopleToDel) return null;

            const rest = { ...visit };
            rest.people = rest.people.filter(
                (p: { id: number }) => p.id !== personToDel,
            );

            delete rest.log_sheet_actual_file;
            delete rest.log_sheet_filename;
            delete rest.navigation_actual_file;
            delete rest.navigation_filename;

            rest.campaign = rest.campaign ?? "";

            const formData = new FormData();

            Object.entries(rest).forEach(([key, value]) => {
                if (key === "people") {
                    value.forEach((p: { id: number; name: string }) => {
                        formData.append("people", String(p.id));
                    });
                } else if (key === "comments") {
                    return;
                } else {
                    formData.append(key, value);
                }
            });

            const res = await patchStationVisitService<ErrorResponse>(
                api,
                visitId,
                formData,
            );
            if ("status" in res) {
                setPeopleMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            } else {
                setPeopleMsg({
                    status: 200,
                    msg: "People Visit updated successfully",
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const filterObservationFiles = (e: string) => {
        if (gnssFiles) {
            const filtered = gnssFiles.filter(
                (file) =>
                    file.filename.toLowerCase().includes(e.toLowerCase()) ||
                    file.description.toLowerCase().includes(e.toLowerCase()),
            );
            setFilteredObservationFiles(filtered);
        }
    };

    const filterOtherFiles = (e: string) => {
        if (files) {
            const filtered = files.filter(
                (file) =>
                    file.filename.toLowerCase().includes(e.toLowerCase()) ||
                    file.description.toLowerCase().includes(e.toLowerCase()),
            );
            setFilteredOtherFiles(filtered);
        }
    };

    const delCampaign = async () => {
        try {
            if (!visitId) return null;
            setLoading(true);

            const rest = { ...visit };
            rest.campaign = "";

            delete rest.log_sheet_actual_file;
            delete rest.log_sheet_filename;
            delete rest.navigation_actual_file;
            delete rest.navigation_filename;

            const formData = new FormData();

            Object.entries(rest).forEach(([key, value]) => {
                if (key === "people") {
                    value.forEach((p: { id: number; name: string }) => {
                        formData.append("people", String(p.id));
                    });
                } else if (key === "comments") {
                    return;
                } else {
                    formData.append(key, value);
                }
            });

            const res = await patchStationVisitService<ErrorResponse>(
                api,
                visitId,
                formData,
            );
            if ("status" in res) {
                setPeopleMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            } else {
                setPeopleMsg({
                    status: 200,
                    msg: "Campaign Visit updated successfully",
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const delVisitFiles = async () => {
        try {
            if (!visitId) return null;
            setLoading(true);

            const rest = { ...visit };

            if (!rest.campaign) delete rest.campaign;

            delete rest.log_sheet_actual_file;
            delete rest.log_sheet_filename;
            delete rest.navigation_actual_file;
            delete rest.navigation_filename;

            const formData = new FormData();
            if (fileType === "logsheet") {
                formData.append("log_sheet_file_delete", "true");
            } else {
                formData.append("navigation_file_delete", "true");
            }

            Object.entries(rest).forEach(([key, value]) => {
                if (key === "people") {
                    value.forEach((p: { id: number; name: string }) => {
                        formData.append("people", String(p.id));
                    });
                } else if (key === "comments") {
                    return;
                } else {
                    formData.append(key, value);
                }
            });

            const res = await patchStationVisitService<ErrorResponse>(
                api,
                visitId,
                formData,
            );
            if ("status" in res) {
                setPeopleMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            } else {
                setPeopleMsg({
                    status: 200,
                    msg: "Visit files updated successfully",
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const delVisitImage = async () => {
        try {
            if (!delPhoto) return null;

            setLoading(true);
            const res = await delStationVisitsImagesService<ErrorResponse>(
                api,
                delPhoto.id,
            );
            if ("status" in res && res.status === "success") {
                setFileMsg({
                    status: res.statusCode,
                    msg: res.msg,
                });
            } else {
                setFileMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleCloseModal = () => {
        closeModal();
    };

    const getAll = () => {
        getVisitById();
        getVisitImagesById();
        getVisitsAttachedFiles();
        getVisitsGnssFiles();
    };

    useEffect(() => {
        getPeople();
        getAll();
    }, [visitId]);

    useEffect(() => {
        dispatch({
            type: "change_value",
            payload: {
                inputName: "date",
                inputValue: formState.date
                    ? formState.date
                    : (visit?.date ?? ""),
            },
        });
    }, [visit]);

    useEffect(() => {
        if (gnssFiles) {
            setFilteredObservationFiles(gnssFiles);
        }
    }, [gnssFiles]);

    useEffect(() => {
        if (files) {
            setFilteredOtherFiles(files);
        }
    }, [files]);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    useEffect(() => {
        if (visit) {
            setRichText(visit?.comments ?? "");
        }
    }, [visit]);

    useEffect(() => {
        dispatch({
            type: "change_value",
            payload: {
                inputName: "comments",
                inputValue: richText,
            },
        });
    }, []);

    const settings = {
        dots: true,
        infinite: true,
        speed: 500,
        slidesToShow: 1,
        slidesToScroll: 1,
        appendDots: (dots: any) => (
            <div>
                <ul className="slick-dots">{dots}</ul>
            </div>
        ),
    };

    const errorBadge = commentsMsg?.errors?.errors?.find(
        (error) => error.attr === "comments",
    );

    return (
        <Modal
            close={false}
            modalId={"VisitDetail"}
            size={"md"}
            handleCloseModal={() => handleCloseModal()}
            setModalState={setStateModal}
        >
            <div className="space-y-4">
                <div className="w-full inline-flex">
                    <h1 className="w-full flex justify-center mb-4 text-2xl font-bold">
                        {edit ? (
                            <input
                                type="date"
                                className="w-full max-w-sm text-center border-b-2 border-gray-300 focus:outline-none focus:border-blue-500 bg-transparent"
                                value={formState["date"] ?? ""}
                                name={"date"}
                                onChange={(e) => {
                                    dispatch({
                                        type: "change_value",
                                        payload: {
                                            inputName: "date",
                                            inputValue: e.target.value,
                                        },
                                    });
                                }}
                            />
                        ) : (
                            visit?.date
                        )}
                    </h1>

                    <button
                        className="flex items-center btn btn-ghost btn-circle"
                        onClick={() => setEdit(!edit)}
                    >
                        <PencilSquareIcon title="edit" className="size-8" />
                    </button>
                </div>
                <div className="grid grid-cols-2 grid-flow-dense">
                    <div className="card bg-base-200 grow shadow-xl mr-4">
                        <h2 className="card-title border-b-2 border-base-300 p-2">
                            General
                        </h2>

                        <div className="card-body">
                            <div className="grid grid-cols-1 gap-6">
                                <div className="flex items-center">
                                    <div className="flex flex-col w-full max-h-60 pr-2 overflow-auto">
                                        <span className="inline-flex items-end justify-between">
                                            <strong className="text-lg">
                                                Campaign:{" "}
                                            </strong>
                                            {edit && (
                                                <button
                                                    className="btn btn-ghost btn-circle ml-2"
                                                    onClick={() => {
                                                        setModals({
                                                            show: true,
                                                            title: "AddVisitCampaign",
                                                            type: "add",
                                                        });
                                                    }}
                                                    disabled={
                                                        visit?.campaign
                                                            ? true
                                                            : false
                                                    }
                                                >
                                                    <PlusCircleIcon
                                                        strokeWidth={1.5}
                                                        stroke="currentColor"
                                                        className="w-8 h-10"
                                                    />
                                                </button>
                                            )}
                                        </span>
                                        <div className="w-full grid grid-cols-1 grid-flow-dense">
                                            <div className="flex flex-col w-full rounded-md bg-neutral-content">
                                                {visit?.campaign ? (
                                                    <div
                                                        className={`flex-grow overflow-hidden ${!edit ? "p-2" : ""} last:border-b-0 border-b-2 border-neutral-200`}
                                                    >
                                                        <div className="p-1 flex w-full justify-between items-center">
                                                            <button
                                                                className="btn btn-ghost btn-circle mr-2"
                                                                style={{
                                                                    display:
                                                                        !edit
                                                                            ? "none"
                                                                            : "",
                                                                }}
                                                                onClick={() => {
                                                                    setModals({
                                                                        show: true,
                                                                        title: "ConfirmDelete",
                                                                        type: "edit",
                                                                    });
                                                                }}
                                                            >
                                                                <TrashIcon className="size-6 text-red-600" />
                                                            </button>
                                                            <div className="flex flex-col w-full text-pretty break-words max-w-full px-2">
                                                                <h2 className="font-semibold text-md">
                                                                    {visit?.campaign
                                                                        ? "(" +
                                                                          visitCampaign?.name +
                                                                          ")" +
                                                                          " " +
                                                                          visitCampaign?.start_date +
                                                                          " - " +
                                                                          visitCampaign?.end_date
                                                                        : "N/A"}
                                                                </h2>
                                                            </div>
                                                        </div>
                                                    </div>
                                                ) : (
                                                    <div className="text-center text-neutral text-xl font-bold w-full rounded-md bg-neutral-content p-6">
                                                        No campaign assigned
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center">
                                    <div className="flex flex-col w-full max-h-60 pr-2 ">
                                        <div className="inline-flex items-end justify-between">
                                            <strong className="text-lg">
                                                People:{" "}
                                            </strong>
                                            {edit && (
                                                <div className="justify-end flex space-x-2">
                                                    <button
                                                        className="btn btn-ghost btn-circle tooltip tooltip-bottom"
                                                        data-tip="Create People"
                                                        onClick={() => {
                                                            setModals &&
                                                                setModals({
                                                                    show: true,
                                                                    title: "EditPerson",
                                                                    type: "add",
                                                                });
                                                        }}
                                                    >
                                                        <UserPlusIcon
                                                            strokeWidth={1.5}
                                                            stroke="currentColor"
                                                            className="w-8 h-8 justify-self-center"
                                                        />
                                                    </button>
                                                    <button
                                                        className="btn btn-ghost btn-circle tooltip tooltip-bottom"
                                                        data-tip="Add existing people"
                                                        onClick={() => {
                                                            setModals({
                                                                show: true,
                                                                title: "AddVisitPeople",
                                                                type: "add",
                                                            });
                                                        }}
                                                    >
                                                        <PlusCircleIcon
                                                            strokeWidth={1.5}
                                                            stroke="currentColor"
                                                            className="w-8 h-10 justify-self-center"
                                                        />
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                        <div className="w-full grid grid-cols-1 grid-flow-dense">
                                            <div className="flex flex-col w-full rounded-md bg-neutral-content max-h-[200px] overflow-y-auto flex-grow">
                                                {visit?.people.length > 0 ? (
                                                    visit?.people.map(
                                                        (p: {
                                                            id: number;
                                                            name: string;
                                                        }) => {
                                                            return (
                                                                <div
                                                                    className={`${!edit ? "p-2" : ""} last:border-b-0 border-b-2 border-neutral-200`}
                                                                    key={p.id}
                                                                >
                                                                    <div className="p-1 flex w-full justify-between items-center">
                                                                        <button
                                                                            className="btn btn-ghost btn-circle mr-2"
                                                                            style={{
                                                                                display:
                                                                                    !edit
                                                                                        ? "none"
                                                                                        : "",
                                                                            }}
                                                                            onClick={() => {
                                                                                setPersonToDel(
                                                                                    p.id,
                                                                                );
                                                                                setModals(
                                                                                    {
                                                                                        show: true,
                                                                                        title: "ConfirmDelete",
                                                                                        type: "edit",
                                                                                    },
                                                                                );
                                                                            }}
                                                                        >
                                                                            <TrashIcon className="size-6 text-red-600" />
                                                                        </button>
                                                                        <div className="flex flex-col w-full text-pretty break-words max-w-full px-2">
                                                                            <h2 className="font-semibold text-md">
                                                                                {p?.name?.toUpperCase()}
                                                                            </h2>
                                                                        </div>
                                                                    </div>
                                                                </div>
                                                            );
                                                        },
                                                    )
                                                ) : (
                                                    <div className="text-center text-neutral text-xl font-bold w-full rounded-md bg-neutral-content p-6">
                                                        No people assigned
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center">
                                    <div className="flex flex-col w-full max-h-60 pr-2 overflow-auto">
                                        <div className="inline-flex items-end justify-between">
                                            <strong className="text-lg">
                                                Log Sheet:{" "}
                                            </strong>
                                            {edit && (
                                                <button
                                                    className="btn btn-ghost btn-circle ml-2"
                                                    onClick={() => {
                                                        setModals({
                                                            show: true,
                                                            title: "AddFile",
                                                            type: "add",
                                                        });
                                                        setFileType("logsheet");
                                                    }}
                                                    disabled={
                                                        visit?.log_sheet_actual_file
                                                            ? true
                                                            : false
                                                    }
                                                >
                                                    <PlusCircleIcon
                                                        strokeWidth={1.5}
                                                        stroke="currentColor"
                                                        className="w-8 h-10"
                                                    />
                                                </button>
                                            )}
                                        </div>
                                        <div className="w-full grid grid-cols-1 grid-flow-dense">
                                            <div className="flex flex-col w-full rounded-md bg-neutral-content">
                                                {visit?.log_sheet_filename ? (
                                                    <div
                                                        className={`flex-grow overflow-hidden ${!edit ? "p-2" : ""} last:border-b-0 border-b-2 border-neutral-200`}
                                                    >
                                                        <div className="p-1 flex w-full justify-between items-center">
                                                            <button
                                                                className="btn btn-ghost btn-circle mr-2"
                                                                style={{
                                                                    display:
                                                                        !edit
                                                                            ? "none"
                                                                            : "",
                                                                }}
                                                                onClick={() => {
                                                                    setFileType(
                                                                        "logsheet",
                                                                    );

                                                                    setModals({
                                                                        show: true,
                                                                        title: "ConfirmDelete",
                                                                        type: "edit",
                                                                    });
                                                                }}
                                                            >
                                                                <TrashIcon className="size-6 text-red-600" />
                                                            </button>
                                                            <div className="flex w-full items-center justify-between text-pretty break-words max-w-full px-2">
                                                                <h2 className="font-semibold text-md">
                                                                    {visit?.log_sheet_filename
                                                                        ? visit.log_sheet_filename
                                                                        : "N/A"}
                                                                </h2>
                                                                <button
                                                                    className="btn-circle btn-ghost flex justify-center"
                                                                    onClick={() => {
                                                                        setModals(
                                                                            {
                                                                                show: true,
                                                                                title: "FileRender",
                                                                                type: "edit",
                                                                            },
                                                                        );
                                                                    }}
                                                                >
                                                                    <BookOpenIcon className="size-6 self-center" />
                                                                </button>
                                                            </div>
                                                        </div>
                                                    </div>
                                                ) : (
                                                    <div className="text-center text-neutral text-xl font-bold w-full rounded-md bg-neutral-content p-6">
                                                        There are no log sheet
                                                        files
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center">
                                    <div className="flex flex-col w-full max-h-60 pr-2 overflow-auto">
                                        <div className="inline-flex items-end justify-between">
                                            <strong className="text-lg">
                                                Navigation File:{" "}
                                            </strong>
                                            {edit && (
                                                <button
                                                    className="btn btn-ghost btn-circle ml-2"
                                                    onClick={() => {
                                                        setModals({
                                                            show: true,
                                                            title: "AddFile",
                                                            type: "add",
                                                        });
                                                        setFileType("navfile");
                                                    }}
                                                    disabled={
                                                        visit?.navigation_actual_file
                                                            ? true
                                                            : false
                                                    }
                                                >
                                                    <PlusCircleIcon
                                                        strokeWidth={1.5}
                                                        stroke="currentColor"
                                                        className="w-8 h-10"
                                                    />
                                                </button>
                                            )}
                                        </div>
                                        <div className="w-full grid grid-cols-1 grid-flow-dense">
                                            <div className="flex flex-col w-full rounded-md bg-neutral-content">
                                                {visit?.navigation_filename ? (
                                                    <div
                                                        className={`flex-grow overflow-hidden ${!edit ? "p-2" : ""} last:border-b-0 border-b-2 border-neutral-200`}
                                                    >
                                                        <div className="p-1 flex w-full justify-between items-center">
                                                            <button
                                                                className="btn btn-ghost btn-circle mr-2"
                                                                style={{
                                                                    display:
                                                                        !edit
                                                                            ? "none"
                                                                            : "",
                                                                }}
                                                                onClick={() => {
                                                                    setFileType(
                                                                        "navfile",
                                                                    );

                                                                    setModals({
                                                                        show: true,
                                                                        title: "ConfirmDelete",
                                                                        type: "edit",
                                                                    });
                                                                }}
                                                            >
                                                                <TrashIcon className="size-6 text-red-600" />
                                                            </button>
                                                            <div className="flex w-full items-center justify-between text-pretty break-words max-w-full px-2">
                                                                <h2 className="font-semibold text-md">
                                                                    {visit?.navigation_filename
                                                                        ? visit.navigation_filename
                                                                        : "N/A"}
                                                                </h2>
                                                                <a
                                                                    className="btn-circle btn-ghost flex justify-center"
                                                                    download={
                                                                        visit.navigation_filename
                                                                    }
                                                                    href={`data:application/octet-stream;base64,${visit.navigation_actual_file}`}
                                                                >
                                                                    <ArrowDownTrayIcon className="size-6 self-center" />
                                                                </a>
                                                            </div>
                                                        </div>
                                                    </div>
                                                ) : (
                                                    <div className="text-center text-neutral text-xl font-bold w-full rounded-md bg-neutral-content p-6">
                                                        There is no navigation
                                                        file
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center">
                                    <div className="w-full flex flex-col space-y-2">
                                        <label
                                            className={`form-control w-full`}
                                            title={
                                                errorBadge
                                                    ? errorBadge.detail
                                                    : ""
                                            }
                                        >
                                            <strong className="text-lg">
                                                Comments:{" "}
                                            </strong>
                                            {edit ? (
                                                <QuillText
                                                    value={richText}
                                                    setValue={setRichText}
                                                    clase="h-32 pb-8"
                                                />
                                            ) : richText ? (
                                                <div
                                                    className="textarea-bordered rounded-md text-xl bg-neutral-content max-h-32 overflow-auto p-4 h-auto"
                                                    dangerouslySetInnerHTML={{
                                                        __html: richText ?? "",
                                                    }}
                                                />
                                            ) : (
                                                <span className="text-center text-neutral text-xl font-bold w-full rounded-md bg-neutral-content p-6">
                                                    There are no comments
                                                </span>
                                            )}

                                            {errorBadge && (
                                                <span className="badge badge-error self-end">
                                                    {errorBadge.code}
                                                </span>
                                            )}
                                        </label>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div className="flex flex-col items-center justify-center">
                        <h3 className="font-bold inline-flex items-center text-xl my-2">
                            Visit Images
                            {edit && (
                                <button
                                    className="btn btn-ghost btn-circle ml-2"
                                    onClick={() => {
                                        setFileType("visitImage");
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
                        </h3>

                        {imagesLoading ? (
                            <div className="flex w-full items-center justify-center h-60 flex-col space-y-4">
                                {" "}
                                <span className="text-xl font-semibold">
                                    Loading images
                                </span>{" "}
                                <Spinner size={"lg"} />{" "}
                            </div>
                        ) : images && images.length === 0 ? (
                            <img
                                className="size-60 object-contain rounded"
                                src={defPhoto}
                                alt={"defphoto"}
                            />
                        ) : images && images.length === 1 ? (
                            <div className="w-full break-words relative text-ellipsis flex flex-col justify-center">
                                <div className="absolute z-10 w-full top-0">
                                    <button
                                        className="btn"
                                        onClick={() => {
                                            setFileType("image");
                                            setModals({
                                                show: true,
                                                title: "ConfirmDelete",
                                                type: "edit",
                                            });
                                            setDelPhoto(images[0]);
                                        }}
                                        style={{
                                            display: !edit ? "none" : "",
                                        }}
                                    >
                                        <TrashIcon className="size-6 text-red-600" />
                                    </button>
                                </div>
                                <img
                                    className="cursor-zoom-in size-70 object-contain rounded justify-self-center"
                                    src={`data:image/*;base64,${images[0].actual_image ?? ""}`}
                                    alt={"photos"}
                                    onMouseEnter={() =>
                                        setBlurPhoto({
                                            blur: true,
                                            id: images[0].id,
                                        })
                                    }
                                    onMouseLeave={() => setBlurPhoto(undefined)}
                                    onClick={() => {
                                        setPhoto({
                                            id: images[0].id,
                                            actual_image:
                                                images[0].actual_image ?? "",
                                            description: images[0].description,
                                            name: images[0].name,
                                        });
                                        setModals({
                                            show: true,
                                            title: "ViewStationPhoto",
                                            type: edit ? "none" : "edit",
                                        });
                                    }}
                                />
                                {images[0].description ? (
                                    <div className="border-t-2 mt-2">
                                        <h3 className="mt-2">Description</h3>
                                        <span className="font-medium">
                                            {images[0].description}
                                        </span>
                                    </div>
                                ) : null}
                            </div>
                        ) : (
                            <div className="w-[400px]">
                                <Slider {...settings}>
                                    {images &&
                                        images.map((img) => {
                                            return (
                                                <div key={img.id} className="">
                                                    <div className="absolute z-10">
                                                        <button
                                                            style={{
                                                                display: !edit
                                                                    ? "none"
                                                                    : "",
                                                            }}
                                                            className="btn "
                                                            onClick={() => {
                                                                setFileType(
                                                                    "image",
                                                                );
                                                                setModals({
                                                                    show: true,
                                                                    title: "ConfirmDelete",
                                                                    type: "edit",
                                                                });
                                                                setDelPhoto(
                                                                    img,
                                                                );
                                                            }}
                                                        >
                                                            <TrashIcon className="size-6 text-red-600" />
                                                        </button>
                                                    </div>
                                                    <img
                                                        className="cursor-zoom-in size-70 object-contain rounded justify-self-center"
                                                        src={`data:image/*;base64,${img.actual_image ?? ""}`}
                                                        alt={"photos"}
                                                        onMouseEnter={() =>
                                                            setBlurPhoto({
                                                                blur: true,
                                                                id: img.id,
                                                            })
                                                        }
                                                        onMouseLeave={() =>
                                                            setBlurPhoto(
                                                                undefined,
                                                            )
                                                        }
                                                        onClick={() => {
                                                            setPhoto({
                                                                id: img.id,
                                                                actual_image:
                                                                    img.actual_image ??
                                                                    "",
                                                                description:
                                                                    img.description,
                                                                name: img.name,
                                                            });
                                                            setModals({
                                                                show: true,
                                                                title: "ViewStationPhoto",
                                                                type: edit
                                                                    ? "none"
                                                                    : "edit",
                                                            });
                                                        }}
                                                    />
                                                    {img.description && (
                                                        <div className="border-t-2 mt-2 break-words">
                                                            <h3 className="mt-2">
                                                                Description
                                                            </h3>
                                                            <span className="font-medium">
                                                                {
                                                                    img.description
                                                                }
                                                            </span>
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })}
                                </Slider>
                            </div>
                        )}
                    </div>
                </div>
                <div className="grid grid-cols-1 space-y-4 grid-flow-dense">
                    <div className="card bg-base-200 grow shadow-xl mr-4">
                        <h2 className="card-title border-b-2 border-base-300 p-2 justify-between">
                            Observation Files (
                            {filteredObservationFiles?.length ?? 0})
                            {gnssFiles && gnssFiles.length > 0 && (
                                <form
                                    action=""
                                    className="absolute flex items-center left-1/2 transform -translate-x-1/2"
                                >
                                    <input
                                        type="text"
                                        className="rounded-md text-md input input-sm input-bordered"
                                        onChange={(e) => {
                                            filterObservationFiles(
                                                e.target.value,
                                            );
                                        }}
                                        placeholder="Search"
                                    />
                                    <button
                                        className="btn btn-ghost btn-circle ml-2"
                                        onClick={(e) => {
                                            handleClearObservationFilter(e);
                                        }}
                                    >
                                        <svg
                                            xmlns="http://www.w3.org/2000/svg"
                                            fill="none"
                                            viewBox="0 0 24 24"
                                            strokeWidth="1.5"
                                            stroke="currentColor"
                                            className="size-8"
                                        >
                                            <path
                                                strokeLinecap="round"
                                                strokeLinejoin="round"
                                                d="m9.75 9.75 4.5 4.5m0-4.5-4.5 4.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
                                            />
                                        </svg>
                                    </button>
                                </form>
                            )}
                            {edit && (
                                <button
                                    className="btn btn-ghost btn-circle ml-2"
                                    onClick={() => {
                                        setModals({
                                            show: true,
                                            title: "AddFile",
                                            type: "add",
                                        });
                                        setFileType("gnss");
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
                            className={`card-body ${showGnssFiles ? "overflow-y-auto max-h-80 scrollbar-base" : ""}`}
                        >
                            <div
                                className={`grid 
                                    ${gnssFiles && gnssFiles.length > 1 ? "grid-cols-2 md:grid-cols-1" : "grid-cols-1"} 
                                    grid-flow-dense gap-2`}
                            >
                                {gnssFiles &&
                                gnssFiles.length > 0 &&
                                filteredObservationFiles ? (
                                    filteredObservationFiles
                                        .slice(
                                            0,
                                            showGnssFiles
                                                ? gnssFiles.length
                                                : 4,
                                        )
                                        .map((f) => {
                                            return (
                                                <div
                                                    className="flex items-center w-full rounded-md bg-neutral-content"
                                                    key={f.description + f.id}
                                                >
                                                    <div className="flex-grow overflow-hidden ">
                                                        <div className="p-6 flex w-full justify-between items-center">
                                                            <button
                                                                className="btn btn-ghost btn-circle mr-4"
                                                                style={{
                                                                    display:
                                                                        !edit
                                                                            ? "none"
                                                                            : "",
                                                                }}
                                                                onClick={() => {
                                                                    setFileType(
                                                                        "gnss",
                                                                    );

                                                                    setModals({
                                                                        show: true,
                                                                        title: "ConfirmDelete",
                                                                        type: "edit",
                                                                    });
                                                                    setFileToDel(
                                                                        f.id,
                                                                    );
                                                                }}
                                                            >
                                                                <TrashIcon className="size-8 text-red-600" />
                                                            </button>
                                                            <div className="flex flex-col w-8/12 text-pretty break-words max-w-full">
                                                                <h3 className="text-slate-500">
                                                                    ({f.size})
                                                                </h3>
                                                                <h2
                                                                    className="card-title truncate"
                                                                    title={
                                                                        f.filename
                                                                    }
                                                                >
                                                                    {
                                                                        f.filename
                                                                    }{" "}
                                                                </h2>
                                                                <p className="w-100 truncate">
                                                                    {
                                                                        f.description
                                                                    }
                                                                </p>
                                                            </div>
                                                            <a
                                                                className="btn-circle btn-ghost cursor-pointer flex justify-center w-4/12"
                                                                download={
                                                                    f.filename
                                                                }
                                                                onClick={async () => {
                                                                    if (!edit) {
                                                                        const res =
                                                                            await getVisitGnssFileById(
                                                                                f.id,
                                                                            );
                                                                        if (
                                                                            res
                                                                        ) {
                                                                            const link =
                                                                                document.createElement(
                                                                                    "a",
                                                                                );

                                                                            link.href = `data:application/octet-stream;base64,${res.actual_file}`;
                                                                            link.download =
                                                                                res.filename;
                                                                            link.click();
                                                                        }
                                                                    } else if (
                                                                        edit
                                                                    ) {
                                                                        setFileType(
                                                                            "gnss",
                                                                        );
                                                                        setModals(
                                                                            {
                                                                                show: true,
                                                                                title: "AddFile",
                                                                                type: "edit",
                                                                            },
                                                                        );
                                                                        setFileToEdit(
                                                                            f,
                                                                        );
                                                                    }
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
                                                                        className="size-8 self-center"
                                                                    >
                                                                        <path
                                                                            strokeLinecap="round"
                                                                            strokeLinejoin="round"
                                                                            d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10"
                                                                        />
                                                                    </svg>
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
                                        There are no observation files
                                    </div>
                                )}
                            </div>
                        </div>

                        {gnssFiles && gnssFiles.length > 4 && (
                            <div className="text-center my-4 font-bold">
                                {!showGnssFiles ? (
                                    <button
                                        onClick={() => handleShowMore("gnss")}
                                    >
                                        Show More
                                    </button>
                                ) : (
                                    <button
                                        onClick={() => handleShowLess("gnss")}
                                    >
                                        Show Less
                                    </button>
                                )}
                            </div>
                        )}
                    </div>
                    <div className="card bg-base-200 grow shadow-xl mr-4">
                        <h2 className="card-title border-b-2 border-base-300 p-2 justify-between">
                            Other Files ({filteredOtherFiles?.length ?? 0})
                            {files && files.length > 0 && (
                                <form
                                    action=""
                                    className="absolute flex items-center left-1/2 transform -translate-x-1/2"
                                >
                                    <input
                                        type="text"
                                        className=" rounded-md text-md input input-sm input-bordered"
                                        onChange={(e) => {
                                            filterOtherFiles(e.target.value);
                                        }}
                                        placeholder="Search"
                                    />
                                    <button
                                        className="btn btn-ghost btn-circle ml-2"
                                        onClick={(e) => {
                                            handleClearOtherFilter(e);
                                        }}
                                    >
                                        <svg
                                            xmlns="http://www.w3.org/2000/svg"
                                            fill="none"
                                            viewBox="0 0 24 24"
                                            strokeWidth="1.5"
                                            stroke="currentColor"
                                            className="size-8"
                                        >
                                            <path
                                                strokeLinecap="round"
                                                strokeLinejoin="round"
                                                d="m9.75 9.75 4.5 4.5m0-4.5-4.5 4.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
                                            />
                                        </svg>
                                    </button>
                                </form>
                            )}
                            {edit && (
                                <button
                                    className="btn btn-ghost btn-circle ml-2"
                                    onClick={() => {
                                        setModals({
                                            show: true,
                                            title: "AddFile",
                                            type: "add",
                                        });
                                        setFileType("other");
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
                            className={`card-body ${showFiles ? "overflow-y-auto max-h-80 scrollbar-base" : ""}`}
                        >
                            <div
                                className={`grid ${files && files.length > 1 ? "grid-cols-2 md:grid-cols-1" : "grid-cols-1"} grid-flow-dense gap-2`}
                            >
                                {files &&
                                files.length > 0 &&
                                filteredOtherFiles ? (
                                    filteredOtherFiles
                                        .slice(
                                            0,

                                            showFiles ? files.length : 4,
                                        )
                                        .map((f) => {
                                            return (
                                                <div
                                                    className="flex items-center w-full rounded-md bg-neutral-content"
                                                    key={f.description + f.id}
                                                >
                                                    <div className="flex-grow overflow-hidden ">
                                                        <div className="p-6 flex w-full justify-between items-center">
                                                            <button
                                                                className="btn btn-ghost btn-circle mr-4"
                                                                style={{
                                                                    display:
                                                                        !edit
                                                                            ? "none"
                                                                            : "",
                                                                }}
                                                                onClick={() => {
                                                                    setFileType(
                                                                        "other",
                                                                    );

                                                                    setModals({
                                                                        show: true,
                                                                        title: "ConfirmDelete",
                                                                        type: "edit",
                                                                    });
                                                                    setFileToDel(
                                                                        f.id,
                                                                    );
                                                                }}
                                                            >
                                                                <TrashIcon className="size-8 text-red-600" />
                                                            </button>
                                                            <div className="flex flex-col w-8/12 text-pretty break-words max-w-full">
                                                                <h2
                                                                    className="card-title truncate"
                                                                    title={
                                                                        f.filename
                                                                    }
                                                                >
                                                                    {f.filename}
                                                                </h2>
                                                                <p>
                                                                    {
                                                                        f.description
                                                                    }
                                                                </p>
                                                            </div>
                                                            <a
                                                                className="btn-circle btn-ghost cursor-pointer flex justify-center w-4/12"
                                                                onClick={async () => {
                                                                    if (!edit) {
                                                                        const res =
                                                                            await getVisitAttachedFileById(
                                                                                f.id,
                                                                            );
                                                                        if (
                                                                            res
                                                                        ) {
                                                                            const link =
                                                                                document.createElement(
                                                                                    "a",
                                                                                );

                                                                            link.href = `data:application/octet-stream;base64,${res.actual_file}`;
                                                                            link.download =
                                                                                res.filename;
                                                                            link.click();
                                                                        }
                                                                    } else if (
                                                                        edit
                                                                    ) {
                                                                        setFileType(
                                                                            "other",
                                                                        );
                                                                        setModals(
                                                                            {
                                                                                show: true,
                                                                                title: "AddFile",
                                                                                type: "edit",
                                                                            },
                                                                        );
                                                                        setFileToEdit(
                                                                            f,
                                                                        );
                                                                    }
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
                                                                        className="size-8 self-center"
                                                                    >
                                                                        <path
                                                                            strokeLinecap="round"
                                                                            strokeLinejoin="round"
                                                                            d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10"
                                                                        />
                                                                    </svg>
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
                                        There are no other files
                                    </div>
                                )}
                            </div>
                        </div>

                        {files && files.length > 4 && (
                            <div className="text-center my-4 font-bold">
                                {!showFiles ? (
                                    <button
                                        onClick={() => handleShowMore("other")}
                                    >
                                        Show More
                                    </button>
                                ) : (
                                    <button
                                        onClick={() => handleShowLess("other")}
                                    >
                                        Show Less
                                    </button>
                                )}
                            </div>
                        )}
                    </div>
                    <Alert msg={commentsMsg} />

                    {edit && (
                        <div className="flex justify-center">
                            <button
                                className="w-36 align btn btn-success rounded my-2"
                                onClick={() => patchVisit()}
                                disabled={
                                    apiOkStatuses.includes(
                                        commentsMsg?.status ?? 0,
                                    ) || commentLoading
                                }
                            >
                                {commentLoading && (
                                    <div
                                        className="inline-block size-6 mx-2 animate-spin rounded-full border-4 border-solid border-current border-e-transparent align-[-0.125em] text-white motion-reduce:animate-[spin_1.5s_linear_infinite]"
                                        role="status"
                                    ></div>
                                )}
                                UPDATE
                            </button>
                        </div>
                    )}
                </div>
            </div>
            {modals && modals?.title === "AddFile" && (
                <AddFileModal
                    id={visitId}
                    visit={visit}
                    pageType={"visit"}
                    fileType={fileType ?? ""}
                    setStateModal={setModals}
                    reFetch={() => {
                        setFileType(undefined);
                        getAll();
                    }}
                    type={modals.type}
                    fileToEdit={fileToEdit}
                    setFileToEdit={setFileToEdit}
                />
            )}

            {modals && modals?.title === "EditPerson" && (
                <StationPeopleModal
                    Person={person}
                    modalType={modals.type}
                    setStateModal={setModals}
                    setPerson={setPerson}
                    people={people}
                    reFetch={() => getPeople()} // En este caso closeModal es el fetch de visits que hace la pagina.
                />
            )}

            {modals && modals?.title === "AddVisitPeople" && (
                <VisitPeopleModal
                    people={people}
                    visit={visit}
                    setStateModal={setModals}
                    reFetch={() => getAll()} // En este caso closeModal es el fetch de visits que hace la pagina.
                />
            )}

            {modals && modals?.title === "AddVisitCampaign" && (
                <VisitCampaignModal
                    campaigns={campaigns}
                    visit={visit}
                    setStateModal={setModals}
                    reFetch={() => {
                        getAll();
                    }}
                    // En este caso closeModal es el fetch de visits que hace la pagina.
                />
            )}

            {modals && modals?.title === "ViewStationPhoto" && (
                <ImageModal
                    photo={photo ?? undefined}
                    visit={true}
                    closeModal={() => {
                        setPhoto(undefined);
                    }}
                    refetch={getVisitImagesById}
                    setStateModal={setModals}
                    type={modals.type}
                />
            )}

            {modals && modals.title === "FileRender" && (
                <RenderFileModal
                    file={`data:application/pdf;base64,${visit?.log_sheet_actual_file}`}
                    filename={visit?.log_sheet_filename}
                    closeModal={() => undefined}
                    setStateModal={setModals}
                />
            )}

            {modals && modals?.title === "ConfirmDelete" && (
                <ConfirmDeleteModal
                    loading={loading}
                    msg={fileMsg || peopleMsg}
                    confirmRemove={() =>
                        fileType
                            ? fileType === "gnss"
                                ? delVisitGnssFile()
                                : fileType === "other"
                                  ? delVisitAttachedFile()
                                  : fileType === "image"
                                    ? delVisitImage()
                                    : delVisitFiles()
                            : personToDel
                              ? delPeople()
                              : delCampaign()
                    }
                    closeModal={() => {
                        setModals({
                            show: false,
                            title: "",
                            type: "edit",
                        });
                        setFileType(undefined);
                        setFileToDel(undefined);
                        setDelPhoto(undefined);
                        setFileMsg(undefined);
                        setPeopleMsg(undefined);
                        setPersonToDel(undefined);
                        getAll();
                    }}
                />
            )}
        </Modal>
    );
};

export default StationVisitDetailModal;

import { useEffect, useState } from "react";
import { Alert, ConfirmDeleteModal, Modal } from "@componentsReact";
import {
    delStationTypesService,
    patchStationTypesService,
    postStationTypesService,
} from "@services";
import { useFormReducer, useAuth, useApi } from "@hooks";
import { apiOkStatuses, showModal } from "@utils";

import {
    Errors,
    ErrorResponse,
    ExtendedStationStatus,
    StationTypeData,
} from "@types";

interface Props {
    StationType: StationTypeData | undefined;
    modalType: string;
    reFetch: () => void;
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
    setStationType: React.Dispatch<
        React.SetStateAction<StationTypeData | undefined>
    >;
}

const StationTypesModal = ({
    StationType,
    modalType,
    reFetch,
    setStateModal,
    setStationType,
}: Props) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const [loading, setLoading] = useState<boolean>(false);
    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);
    const [deleteMsg, setDeleteMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const { formState, dispatch } = useFormReducer<{
        id: string;
        name: string;
        search_icon_on_assets_folder: string;
        actual_image: string | File;
    }>({
        id: "",
        name: "",
        search_icon_on_assets_folder: "",
        actual_image: "",
    });

    function base64ToFile(
        base64String: string,
        fileName: string,
        mimeType: string = "image/jpeg",
    ): File {
        const base64WithoutPrefix = base64String?.includes("base64,")
            ? base64String.split("base64,")[1]
            : base64String;

        const byteString = atob(base64WithoutPrefix);

        const arrayBuffer = new ArrayBuffer(byteString.length);
        const intArray = new Uint8Array(arrayBuffer);

        for (let i = 0; i < byteString.length; i++) {
            intArray[i] = byteString.charCodeAt(i);
        }

        const blob = new Blob([arrayBuffer], { type: mimeType });

        return new File([blob], fileName, { type: mimeType });
    }

    useEffect(() => {
        if (StationType) {
            dispatch({
                type: "set",
                payload: StationType,
            });
        }
    }, [StationType]); // eslint-disable-line

    const postType = async () => {
        try {
            setLoading(true);
            const formData = new FormData();

            formData.append("name", formState.name);
            formData.append("icon", formState.actual_image);

            const res = await postStationTypesService<
                ExtendedStationStatus | ErrorResponse
            >(api, formData);
            if ("status" in res) {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: "Station Type added successfully",
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const patchType = async () => {
        try {
            setLoading(true);

            const formData = new FormData();

            const imageBase64 =
                (StationType?.actual_image as string) ?? undefined;

            const imageFile =
                typeof formState.actual_image === "string"
                    ? imageBase64
                        ? base64ToFile(imageBase64, "image.jpg", "image/jpeg")
                        : formState.actual_image
                    : formState.actual_image;

            formData.append("name", formState.name);
            formData.append("icon", imageFile);

            const res = await patchStationTypesService<
                ExtendedStationStatus | ErrorResponse
            >(api, Number(StationType?.id), formData);
            if ("status" in res) {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: "Station Type edited successfully",
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const delStatus = async () => {
        try {
            setLoading(true);

            const res = await delStationTypesService<ErrorResponse>(
                api,
                Number(StationType?.id),
            );
            if ("status" in res && res.status === "success") {
                setDeleteMsg({
                    status: res.statusCode,
                    msg: res.msg,
                });
            } else {
                setDeleteMsg({
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
        setStationType(undefined);
        reFetch();
    };

    const handleChange = (e: HTMLInputElement | HTMLSelectElement) => {
        const { name, value } = e;

        dispatch({
            type: "change_value",
            payload: {
                inputName: name,
                inputValue: value,
            },
        });
    };

    const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        if (modalType === "edit") {
            patchType();
        } else if (modalType === "add") {
            postType();
        }
    };

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    return (
        <Modal
            close={false}
            modalId={"EditStationType"}
            size={"smPlus"}
            handleCloseModal={() => handleCloseModal()}
            setModalState={setStateModal}
        >
            <div className="w-full flex grow mb-2">
                <h3 className="font-bold text-center text-2xl my-2 w-full self-center">
                    {modalType?.charAt(0).toUpperCase() + modalType?.slice(1)}
                </h3>
            </div>
            <form className="form-control space-y-4" onSubmit={handleSubmit}>
                <div className="form-control space-y-2">
                    {Object.keys(formState || {}).map((key, index) => {
                        const errorBadge = msg?.errors?.errors?.find(
                            (error) => error.attr === key,
                        );
                        const optionalFields: string[] = [];
                        if (
                            key !== "actual_image" &&
                            key !== "search_icon_on_assets_folder"
                        ) {
                            return (
                                <div
                                    className="flex items-center gap-2"
                                    key={key + index}
                                >
                                    <label
                                        key={index}
                                        id={key}
                                        className={`w-full input input-bordered flex items-center gap-2 ${errorBadge ? "input-error" : ""}`}
                                        title={
                                            errorBadge ? errorBadge.detail : ""
                                        }
                                    >
                                        <div className="label">
                                            <span className="font-bold">
                                                {key
                                                    .toUpperCase()
                                                    .replace("_", " ")
                                                    .replace("_", " ")}
                                            </span>
                                        </div>
                                        <input
                                            type="text"
                                            name={key}
                                            value={
                                                typeof formState[
                                                    key as keyof typeof formState
                                                ] === "object"
                                                    ? (
                                                          formState[
                                                              key as keyof typeof formState
                                                          ] as File
                                                      ).name
                                                    : ((formState[
                                                          key as keyof typeof formState
                                                      ] as string) ?? "")
                                            }
                                            onChange={(e) => {
                                                handleChange(e.target);
                                            }}
                                            className="grow "
                                            autoComplete="off"
                                            disabled={key === "id"}
                                        />
                                        {errorBadge && (
                                            <span className="badge badge-error">
                                                {errorBadge.code}
                                            </span>
                                        )}
                                        {optionalFields.includes(key) && (
                                            <span className="badge badge-secondary">
                                                Optional
                                            </span>
                                        )}
                                    </label>
                                </div>
                            );
                        } else if (key === "actual_image" && formState[key]) {
                            const base64Str = "data:image/png;base64,";
                            return (
                                <div
                                    key={key + index}
                                    className="flex items-center gap-2 w-full"
                                >
                                    {formState.actual_image instanceof File ? (
                                        <img
                                            src={URL.createObjectURL(
                                                formState.actual_image,
                                            )}
                                            alt=""
                                            className="w-full h-32 object-contain"
                                        />
                                    ) : (
                                        <img
                                            src={
                                                base64Str +
                                                formState.actual_image
                                            }
                                            alt=""
                                            className="w-full h-32 object-contain"
                                        />
                                    )}
                                </div>
                            );
                        }
                    })}
                    {
                        <div className="flex items-center gap-2">
                            <input
                                type="file"
                                name="actual_image"
                                accept=".png,.jpg,.jpeg"
                                onChange={(e) => {
                                    if (
                                        e.target.files &&
                                        e.target.files.length === 1
                                    ) {
                                        dispatch({
                                            type: "change_value",
                                            payload: {
                                                inputName: "actual_image",
                                                inputValue: e.target.files[0],
                                            },
                                        });
                                    }
                                }}
                                className="file-input file-input-bordered w-full"
                            />
                        </div>
                    }
                </div>
                <Alert msg={msg} />
                {loading && (
                    <div className="w-full text-center">
                        <span className="loading loading-spinner loading-lg self-center"></span>
                    </div>
                )}
                <div className="flex w-full justify-center space-x-4">
                    {modalType === "edit" && (
                        <button
                            className="btn btn-error w-5/12"
                            type="button"
                            disabled={
                                apiOkStatuses.includes(
                                    Number(deleteMsg?.status),
                                ) ||
                                apiOkStatuses.includes(Number(msg?.status)) ||
                                loading
                            }
                            onClick={() =>
                                setModals({
                                    show: true,
                                    title: "ConfirmDelete",
                                    type: "edit",
                                })
                            }
                        >
                            Remove
                        </button>
                    )}
                    <button
                        type="submit"
                        className="btn btn-success w-5/12"
                        disabled={
                            apiOkStatuses.includes(Number(deleteMsg?.status)) ||
                            apiOkStatuses.includes(Number(msg?.status)) ||
                            loading
                        }
                    >
                        Submit
                    </button>
                </div>
            </form>
            {modals && modals?.title === "ConfirmDelete" && (
                <ConfirmDeleteModal
                    msg={deleteMsg}
                    loading={loading}
                    confirmRemove={() => delStatus()}
                    closeModal={() => {
                        setModals({
                            show: false,
                            title: "",
                            type: "edit",
                        });
                        setMsg(undefined);
                    }}
                />
            )}
        </Modal>
    );
};

export default StationTypesModal;

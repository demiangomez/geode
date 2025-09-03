import { Alert, ConfirmDeleteModal, Modal, Spinner } from "@components/index";
import { useFormReducer } from "@hooks/index";
import {
    deleteSourcesFormatsService,
    postSourcesFormatsService,
    putSourcesFormatsService,
} from "@services";
import { Errors, SourcesFormatData, ErrorResponse } from "@types";
import { showModal } from "@utils/index";
import { SOURCES_FORMATS_STATE } from "@utils/reducerFormStates";
import { AxiosInstance } from "axios";
import { useEffect, useState } from "react";

interface SourcesFormatsTableModalProps {
    sourceFormat: SourcesFormatData | undefined;
    handleClose: () => void;
    refetch: () => void;
    api: AxiosInstance;
    type: "add" | "edit" | "none";
}

const SourcesFormatsTableModal = ({
    sourceFormat,
    handleClose,
    refetch,
    api,
    type,
}: SourcesFormatsTableModalProps) => {
    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const camps = ["format"];

    const errorBadge = msg?.errors?.errors?.map((e) => e.attr);

    const [success, setSuccess] = useState<boolean>(false);

    const [loading, setLoading] = useState<boolean>(false);

    const { formState, dispatch } = useFormReducer(SOURCES_FORMATS_STATE);

    const [deleteModals, setDeleteModals] = useState<
        | {
              show: boolean;
              title: string;
              type: "add" | "edit" | "none";
          }
        | undefined
    >(undefined);

    const handleSubmit = async () => {
        if (type === "add") {
            postSourcesFormats();
        } else if (type === "edit") {
            putSourcesFormats();
        }
    };

    const handleCancel = () => {
        handleClose();
        dispatch({
            type: "clear",
        });
    };

    const handleRemove = () => {
        deleteSourcesServers();
    };

    const postSourcesFormats = async () => {
        try {
            setLoading(true);
            const res = await postSourcesFormatsService<ErrorResponse>(
                api,
                formState,
            );
            if (res.statusCode === 201) {
                setMsg({
                    status: res.statusCode,
                    msg: "Sources Server Created",
                });
                setSuccess(true);
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
            setDeleteModals(undefined);
        }
    };

    const putSourcesFormats = async () => {
        try {
            setLoading(true);
            const res = await putSourcesFormatsService<ErrorResponse>(
                api,
                Number(formState.api_id),
                formState,
            );
            if (res.statusCode === 200) {
                setMsg({
                    status: res.statusCode,
                    msg: "Sources Server Updated",
                });
                setSuccess(true);
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
            setDeleteModals(undefined);
        }
    };

    const deleteSourcesServers = async () => {
        try {
            setLoading(true);
            const res = await deleteSourcesFormatsService<ErrorResponse>(
                api,
                Number(formState.api_id),
            );
            if (res.statusCode === 204) {
                setMsg({
                    status: res.statusCode,
                    msg: "Sources Server Deleted",
                });
                setSuccess(true);
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
            setDeleteModals(undefined);
        }
    };

    useEffect(() => {
        if (sourceFormat) {
            dispatch({
                type: "set",
                payload: sourceFormat,
            });
        }
    }, [sourceFormat]);

    useEffect(() => {
        deleteModals && deleteModals.show && showModal(deleteModals.title);
    }, [deleteModals]);
    return (
        <Modal
            modalId="Source Format"
            close={false}
            size="sm"
            handleCloseModal={() => {
                handleClose();
                setMsg(undefined);
                success && refetch();
            }}
        >
            <div className="flex flex-col justify-cenmter items-center gap-4">
                <h2 className="text-2xl font-bold">
                    {type === "add"
                        ? "Add Source"
                        : type === "edit"
                          ? "Edit Source"
                          : "View Source"}
                </h2>
                <div className="flex flex-col gap-3 w-full">
                    {camps.map((camp) => (
                        <div key={camp} className="w-full">
                            <label
                                className={`w-full input input-bordered flex items-center gap-2 ${
                                    errorBadge?.includes(camp)
                                        ? "input-error"
                                        : ""
                                } `}
                                title={
                                    errorBadge?.includes(camp)
                                        ? msg?.errors?.errors.find(
                                              (e) => e.attr === camp,
                                          )?.detail
                                        : (
                                              formState[
                                                  camp as keyof typeof formState
                                              ] ?? ""
                                          ).toString()
                                }
                            >
                                <div className="label">
                                    <span className="font-bold">
                                        {camp === "server_id"
                                            ? "SERVER"
                                            : camp
                                                  .replace("_", " ")
                                                  .toUpperCase()}
                                    </span>
                                </div>
                                <input
                                    className="grow"
                                    type="text"
                                    value={
                                        formState[
                                            camp as keyof typeof formState
                                        ] ?? ""
                                    }
                                    onChange={(e) => {
                                        dispatch({
                                            type: "change_value",
                                            payload: {
                                                inputName: camp,
                                                inputValue: e.target.value,
                                            },
                                        });
                                    }}
                                />
                                {errorBadge && errorBadge.includes(camp) && (
                                    <span className="badge badge-error absolute right-0 mb-12 mr-2">
                                        {errorBadge.includes(camp)
                                            ? msg?.errors?.errors.find(
                                                  (e) => e.attr === camp,
                                              )?.code
                                            : ""}
                                    </span>
                                )}
                            </label>
                        </div>
                    ))}
                </div>
                <div className="flex flex-row justify-center items-center gap-2">
                    <div>
                        <button
                            className="btn btn-success btn-md w-[100px]"
                            onClick={handleSubmit}
                            disabled={loading || success}
                        >
                            {loading && <Spinner size="md" />}
                            <span className="font-bold">
                                {type === "edit" ? "Update" : "Add"}
                            </span>
                        </button>
                    </div>
                    <button
                        className="btn btn-error btn-md w-[100px]"
                        onClick={() => {
                            if (type === "edit") {
                                setDeleteModals({
                                    show: true,
                                    title: "ConfirmDelete",
                                    type: "edit",
                                });
                            } else {
                                handleCancel();
                            }
                        }}
                        disabled={loading || success}
                    >
                        {type === "edit" ? "Remove" : "Cancel"}
                    </button>
                </div>
                <Alert msg={msg} />
            </div>
            {deleteModals?.show && deleteModals?.type === "edit" && (
                <ConfirmDeleteModal
                    confirmRemove={handleRemove}
                    closeModal={() => {}}
                />
            )}
        </Modal>
    );
};

export default SourcesFormatsTableModal;

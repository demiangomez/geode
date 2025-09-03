import {
    Alert,
    ConfirmDeleteModal,
    Menu,
    MenuButton,
    MenuContent,
    Modal,
    Spinner,
} from "@components/index";
import { useFormReducer } from "@hooks/index";
import {
    deleteSourcesServersService,
    postSourcesServersService,
    putSourcesServersService,
} from "@services";
import {
    Errors,
    SourcesFormatData,
    SourcesServerData,
    ErrorResponse,
} from "@types";
import { showModal } from "@utils/index";
import { SOURCES_SERVERS_STATE } from "@utils/reducerFormStates";
import { AxiosInstance } from "axios";
import { useEffect, useState } from "react";

interface SourcesServersTableModalProps {
    handleClose: () => void;
    type: "add" | "edit" | "none" | undefined;
    refetch: () => void;
    sourcesFormats: SourcesFormatData[] | undefined;
    sourceServer: SourcesServerData | undefined;
    api: AxiosInstance;
}

const SourcesServersTableModal = ({
    handleClose,
    type,
    refetch,
    sourcesFormats,
    sourceServer,
    api,
}: SourcesServersTableModalProps) => {
    const camps = [
        "protocol",
        "fqdn",
        "username",
        "password",
        "path",
        "format",
    ];

    const protocols = ["FTP", "HTTP", "SFTP", "HTTPS", "FTPS"];

    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [showMenu, setShowMenu] = useState<
        { show: boolean; type: string } | undefined
    >({ show: false, type: "" });

    const errorBadge = msg?.errors?.errors?.map((e) => e.attr);

    const [success, setSuccess] = useState<boolean>(false);

    const [loading, setLoading] = useState<boolean>(false);

    const { formState, dispatch } = useFormReducer(SOURCES_SERVERS_STATE);

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
            postSourcesServers();
        } else if (type === "edit") {
            putSourcesServers();
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

    const postSourcesServers = async () => {
        try {
            setLoading(true);
            const res = await postSourcesServersService<ErrorResponse>(
                api,
                formState,
            );
            if (res.statusCode >= 200 && res.statusCode < 300) {
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

    const putSourcesServers = async () => {
        try {
            setLoading(true);
            const res = await putSourcesServersService<ErrorResponse>(
                api,
                Number(formState.server_id),
                formState,
            );
            if (res.statusCode >= 200 && res.statusCode < 300) {
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
            const res = await deleteSourcesServersService<ErrorResponse>(
                api,
                Number(formState.server_id),
            );
            if (res.statusCode >= 200 && res.statusCode < 300) {
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
        if (sourceServer) {
            dispatch({
                type: "set",
                payload: sourceServer,
            });
        }
    }, [sourceServer]);

    useEffect(() => {
        deleteModals && deleteModals.show && showModal(deleteModals.title);
    }, [deleteModals]);

    return (
        <Modal
            modalId="Sources Servers"
            close={false}
            size="md"
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
                                    ref={(input) => {
                                        if (
                                            input &&
                                            showMenu?.show &&
                                            showMenu?.type === camp
                                        ) {
                                            input.focus();
                                        }
                                    }}
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
                                    onClick={() => {
                                        setShowMenu({ show: true, type: camp });
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
                                {["format", "protocol"].includes(camp) && (
                                    <MenuButton
                                        setShowMenu={setShowMenu}
                                        showMenu={showMenu}
                                        typeKey={camp}
                                    />
                                )}
                            </label>
                            {showMenu?.show &&
                                showMenu?.type === "format" &&
                                camp === "format" && (
                                    <Menu>
                                        {sourcesFormats
                                            ?.filter((f) =>
                                                f.format
                                                    .toLowerCase()
                                                    .includes(
                                                        formState.format?.toLowerCase() ||
                                                            "",
                                                    ),
                                            )
                                            .map((f) => (
                                                <MenuContent
                                                    key={f.format}
                                                    typeKey={camp}
                                                    value={f.format}
                                                    setShowMenu={setShowMenu}
                                                    dispatch={dispatch}
                                                    alterFunctionWithValue={(
                                                        value,
                                                    ) => {
                                                        dispatch({
                                                            type: "change_value",
                                                            payload: {
                                                                inputName: camp,
                                                                inputValue:
                                                                    sourcesFormats?.find(
                                                                        (s) =>
                                                                            s.format ===
                                                                            value,
                                                                    )?.format ??
                                                                    "",
                                                            },
                                                        });
                                                    }}
                                                />
                                            ))}
                                    </Menu>
                                )}
                            {showMenu?.show &&
                                showMenu?.type === "protocol" &&
                                camp === "protocol" && (
                                    <Menu>
                                        {protocols
                                            .filter((p) =>
                                                p
                                                    .toLowerCase()
                                                    .includes(
                                                        formState.protocol?.toLowerCase() ||
                                                            "",
                                                    ),
                                            )
                                            .map((p) => (
                                                <MenuContent
                                                    key={p}
                                                    typeKey={camp}
                                                    value={p}
                                                    setShowMenu={setShowMenu}
                                                    dispatch={dispatch}
                                                    alterFunctionWithValue={(
                                                        value,
                                                    ) => {
                                                        dispatch({
                                                            type: "change_value",
                                                            payload: {
                                                                inputName: camp,
                                                                inputValue:
                                                                    value,
                                                            },
                                                        });
                                                    }}
                                                />
                                            ))}
                                    </Menu>
                                )}
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

export default SourcesServersTableModal;

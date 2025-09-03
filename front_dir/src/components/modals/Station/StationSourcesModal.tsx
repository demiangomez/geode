import { useEffect, useState } from "react";
import {
    Alert,
    ConfirmDeleteModal,
    Menu,
    MenuButton,
    MenuContent,
    Modal,
    Spinner,
} from "components";

import { AxiosInstance } from "axios";

import {
    putSourcesStationsService,
    postSourcesStationsService,
    deleteSourcesStationsService,
} from "@services";

import { useFormReducer } from "@hooks";

import { showModal } from "@utils";

import { SOURCES_STATIONS_STATE } from "@utils/reducerFormStates";

import {
    Errors,
    SourcesFormatData,
    SourcesServerData,
    SourcesStationsData,
    StationData,
    ErrorResponse,
} from "@types";

interface StationSourcesModalProps {
    sourcesServers: SourcesServerData[] | undefined;
    sourcesFormats: SourcesFormatData[] | undefined;
    sourceStation?: SourcesStationsData;
    station: StationData;
    type: "add" | "edit" | "none";
    handleClose: () => void;
    api: AxiosInstance;
    refetch: () => void;
}

const StationSourcesModel = ({
    sourcesServers,
    sourcesFormats,
    sourceStation,
    type,
    handleClose,
    api,
    station,
    refetch,
}: StationSourcesModalProps) => {
    const { formState, dispatch } = useFormReducer(SOURCES_STATIONS_STATE);

    const [deleteModals, setDeleteModals] = useState<
        | {
              show: boolean;
              title: string;
              type: "add" | "edit" | "none";
          }
        | undefined
    >(undefined);

    const [success, setSuccess] = useState<boolean>(false);

    const [loading, setLoading] = useState<boolean>(false);

    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);
    const [showMenu, setShowMenu] = useState<
        { show: boolean; type: string } | undefined
    >({ show: false, type: "" });

    const errorBadge = msg?.errors?.errors?.map((e) => e.attr);

    const camps = [
        "try_order",
        "path",
        "format",
        "server_id",
        "default_path",
        "default_format",
    ];

    const handleCancel = () => {
        handleClose();
        dispatch({
            type: "clear",
        });
    };

    const postSourcesStation = async () => {
        try {
            setLoading(true);
            const server = sourcesServers?.find(
                (s) =>
                    `${s.fqdn} ${s.protocol} ${s.path} ${s.format}` ===
                    formState.server_id,
            );

            const params = {
                try_order: formState.try_order,
                network_code: station.network_code,
                station_code: station.station_code,
                path: formState.path,
                server_id: server?.server_id.toString() ?? undefined,
                format: formState.format,
            };
            const res = await postSourcesStationsService<ErrorResponse>(
                api,
                params,
            );
            if (res.statusCode >= 200 && res.statusCode < 300) {
                setMsg({
                    status: res.statusCode,
                    msg: "Source Station created successfully",
                });
                setSuccess(true);
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            }
        } catch (error: any) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    const putSourcesStation = async () => {
        try {
            setLoading(true);
            const server = sourcesServers?.find(
                (s) =>
                    `${s.fqdn} ${s.protocol} ${s.path} ${s.format}` ===
                    formState.server_id,
            );
            const params = {
                try_order: formState.try_order,
                network_code: station.network_code,
                station_code: station.station_code,
                path: formState.path,
                server_id: server?.server_id.toString() ?? undefined,
                format: formState.format,
            };
            const res = await putSourcesStationsService<ErrorResponse>(
                api,
                sourceStation?.api_id as number,
                params,
            );
            if (res.statusCode >= 200 && res.statusCode < 300) {
                setMsg({
                    status: res.statusCode,
                    msg: "Source Station updated successfully",
                });
                setSuccess(true);
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            }
        } catch (error: any) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    const handleSubmit = () => {
        if (type === "add") {
            postSourcesStation();
        } else {
            putSourcesStation();
        }
    };

    const handleRemove = async () => {
        try {
            setLoading(true);
            const res = await deleteSourcesStationsService<ErrorResponse>(
                api,
                sourceStation?.api_id as number,
            );
            if (res.statusCode >= 200 && res.statusCode < 300) {
                setMsg({
                    status: res.statusCode,
                    msg: "Source Station removed successfully",
                });
                setSuccess(true);
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            }
        } catch (error: any) {
            console.error(error);
        } finally {
            setLoading(false);
            setDeleteModals(undefined);
        }
    };

    const matchServer = (serverId: number) => {
        const server = sourcesServers?.find((sv) => sv.server_id === serverId);
        if (server) {
            const fqdn = server.fqdn;
            const protocol = server.protocol;
            const path = server.path;
            const format = server.format;
            return `${fqdn} ${protocol} ${path} ${format}`;
        }
        return "";
    };

    const getDefaultFormat = (serverId: number) => {
        const server = sourcesServers?.find((sv) => sv.server_id === serverId);
        if (server) {
            return server.format;
        } else {
            return "";
        }
    };

    const getDefaultPath = (serverId: number) => {
        const server = sourcesServers?.find((sv) => sv.server_id === serverId);
        if (server) {
            return server.path;
        } else {
            return "";
        }
    };

    useEffect(() => {
        if (sourceStation && type === "edit") {
            const stringifiedSourceStation = Object.fromEntries(
                Object.entries(sourceStation).map(([key, value]) => [
                    key,
                    value?.toString(),
                ]),
            );
            dispatch({
                type: "set",
                payload: stringifiedSourceStation,
            });
            const server = sourcesServers?.find(
                (sv) => sourceStation.server_id === sv.server_id,
            );
            dispatch({
                type: "change_value",
                payload: {
                    inputName: "server_id",
                    inputValue: server ? matchServer(server.server_id) : "",
                },
            });
        }
    }, [sourceStation]);

    useEffect(() => {
        deleteModals?.show && showModal(deleteModals.title);
    }, [deleteModals]);
    return (
        <Modal
            modalId="Station Sources"
            close={false}
            size="md"
            handleCloseModal={() => {
                handleClose();
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
                                    disabled={camp.includes("default")}
                                    value={
                                        camp === "default_path"
                                            ? getDefaultPath(
                                                  sourcesServers?.find(
                                                      (s) =>
                                                          `${s.fqdn} ${s.protocol} ${s.path} ${s.format}` ===
                                                          formState.server_id,
                                                  )?.server_id as number,
                                              ) || ""
                                            : camp === "default_format"
                                              ? getDefaultFormat(
                                                    sourcesServers?.find(
                                                        (s) =>
                                                            `${s.fqdn} ${s.protocol} ${s.path} ${s.format}` ===
                                                            formState.server_id,
                                                    )?.server_id as number,
                                                ) || ""
                                              : (formState[
                                                    camp as keyof typeof formState
                                                ] ?? "")
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
                                        setShowMenu({
                                            show: true,
                                            type: camp,
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
                                {["server_id", "format"].includes(camp) && (
                                    <MenuButton
                                        setShowMenu={setShowMenu}
                                        showMenu={showMenu}
                                        typeKey={camp}
                                    />
                                )}
                            </label>
                            {showMenu?.show &&
                                showMenu?.type === "server_id" &&
                                camp === "server_id" && (
                                    <Menu>
                                        {sourcesServers
                                            ?.filter((server) => {
                                                const serverInfo = matchServer(
                                                    server.server_id,
                                                ).toLowerCase();
                                                const searchTerm =
                                                    formState.server_id?.toLowerCase() ||
                                                    "";
                                                const searchWords =
                                                    searchTerm.split(" ");

                                                return searchWords.every(
                                                    (word) =>
                                                        serverInfo.includes(
                                                            word.toLowerCase(),
                                                        ),
                                                );
                                            })
                                            .map((server) => (
                                                <MenuContent
                                                    key={server.server_id}
                                                    typeKey={camp}
                                                    value={matchServer(
                                                        server.server_id,
                                                    )}
                                                    setShowMenu={setShowMenu}
                                                    dispatch={dispatch}
                                                />
                                            ))}
                                    </Menu>
                                )}
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
                    closeModal={() => {
                        setDeleteModals(undefined);
                    }}
                />
            )}
        </Modal>
    );
};

export default StationSourcesModel;

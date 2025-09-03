import { ArrowRightIcon } from "@heroicons/react/24/outline";
import { Errors, SourcesServerData, SourcesStationsData } from "@types";
import { Alert, Modal, Spinner } from "components";
import { useState } from "react";
import { swapTryOrdenService } from "@services";
import { ErrorResponse } from "react-router-dom";
import { AxiosInstance } from "axios";

interface StationChangeTryOrderModalProps {
    sourcesStations: SourcesStationsData[];
    sourcesServers: SourcesServerData[] | undefined;
    handleCloseModal: () => void;
    api: AxiosInstance;
    refetch: () => void;
}

const StationChangeTryOrderModal = ({
    sourcesStations,
    sourcesServers,
    handleCloseModal,
    api,
    refetch,
}: StationChangeTryOrderModalProps) => {
    const [from, setFrom] = useState<number | undefined>(undefined);

    const [to, setTo] = useState<number | undefined>(undefined);

    const [loading, setLoading] = useState<boolean>(false);

    const [successText, setSuccessText] = useState<boolean>(false);

    const handleSubmit = (e: any) => {
        e.preventDefault();
        if (from && to) {
            swapTryOrderService();
        }
    };

    const swapTryOrderService = async () => {
        try {
            setLoading(true);
            await swapTryOrdenService<ErrorResponse>(api, {
                from: from as number,
                to: to as number,
            });
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
            setSuccessText(true);
        }
    };

    const matchServer = (serverId: number) => {
        const server = sourcesServers?.find((sv) => sv.server_id === serverId);
        if (server) {
            return `${server.fqdn} ${server.protocol}`;
        } else {
            return "";
        }
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

    return (
        <Modal
            close={false}
            size="lg"
            handleCloseModal={() => {
                handleCloseModal();
                setTo(undefined);
                setFrom(undefined);
                successText && refetch();
            }}
            modalId="Change Try Order"
        >
            <div
                className={
                    successText
                        ? "flex flex-col items-start justify-between max-h-[56vh] min-w-[40vw] gap-0"
                        : from == to && from !== undefined && to !== undefined
                          ? "flex flex-col items-start justify-between max-h-[56vh] min-w-[40vw] gap-0"
                          : "flex flex-col items-start justify-start max-h-[56vh] min-w-[40vw] gap-0 pb-4"
                }
            >
                <div className="w-full border-b-2 border-gray-300 pb-6 pl-8 pt-6">
                    <h1 className="text-2xl font-bold">Swap Try Order</h1>
                </div>
                <form
                    typeof="submit"
                    onSubmit={(e) => {
                        handleSubmit(e);
                    }}
                    className="w-full flex justify-start items-center flex-col gap-4"
                >
                    <div className="flex justify-center flex-row items-center border-b-2 mt-4 border-gray-300 w-full pb-6">
                        <div className="flex flex-col gap-6 w-full max-w-[50%] pl-6">
                            <h2 className="text-xl font-semibold">
                                Swap From:
                            </h2>
                            <div className="max-h-[20vh] flex flex-col gap-2 overflow-y-auto">
                                {sourcesStations?.map((s, index) => (
                                    <div
                                        key={index}
                                        className="flex justify-center items-center gap-4 p-2 mb-1 hover:bg-gray-400 rounded-md bg-gray-300"
                                    >
                                        <input
                                            className="checkbox checkbox-lg"
                                            type="checkbox"
                                            name="person"
                                            value={s.api_id}
                                            checked={from === s.api_id}
                                            onChange={() =>
                                                from === s.api_id
                                                    ? setFrom(undefined)
                                                    : setFrom(s.api_id)
                                            }
                                        />
                                        <div className="flex flex-row justify-start items-center w-full gap-2">
                                            <div className="flex flex-row gap-2 justify-start items-center">
                                                <label className="text-3xl w-1/4 font-bold text-center">
                                                    {s.try_order}
                                                </label>
                                            </div>
                                            <div className="flex flex-col justify-center items-center w-full">
                                                <label
                                                    title={s.server_id.toString()}
                                                    className="flex justify-center items-center text-2xl w-full text-pretty"
                                                >
                                                    {matchServer(s.server_id)}
                                                </label>
                                                <label className="text-lg text-pretty w-full overflow-auto whitespace-normal break-all text-center">
                                                    {s.path && s.path !== ""
                                                        ? s.path
                                                        : "*" +
                                                          getDefaultPath(
                                                              s.server_id,
                                                          )}
                                                </label>
                                                <label className="text-lg text-pretty">
                                                    {s.format && s.format !== ""
                                                        ? s.format
                                                        : "*" +
                                                          getDefaultFormat(
                                                              s.server_id,
                                                          )}
                                                </label>
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                        <div className="flex flex-col gap-6 w-full max-w-[50%] pl-6">
                            <h2 className="text-xl font-semibold">Swap To:</h2>
                            <div className="max-h-[20vh] flex flex-col gap-2 overflow-y-auto">
                                {sourcesStations?.map((s, index) => (
                                    <div
                                        key={index}
                                        className="flex justify-center items-center gap-4 p-2 mb-1 hover:bg-gray-400 rounded-md bg-gray-300"
                                    >
                                        <input
                                            className="checkbox checkbox-lg"
                                            type="checkbox"
                                            name="person"
                                            value={s.api_id}
                                            checked={to === s.api_id}
                                            onChange={() =>
                                                to === s.api_id
                                                    ? setTo(undefined)
                                                    : setTo(s.api_id)
                                            }
                                        />
                                        <div className="flex flex-row justify-start items-center w-full gap-2">
                                            <div className="flex flex-row gap-2 justify-start items-center">
                                                <label className="text-3xl w-1/4 font-bold text-center">
                                                    {s.try_order}
                                                </label>
                                            </div>
                                            <div className="flex flex-col justify-center items-center w-full">
                                                <label
                                                    title={s.server_id.toString()}
                                                    className="flex justify-center items-center text-2xl w-full text-pretty"
                                                >
                                                    {matchServer(s.server_id)}
                                                </label>
                                                <label className="text-lg text-pretty w-full overflow-auto whitespace-normal break-all text-center">
                                                    {s.path && s.path !== ""
                                                        ? s.path
                                                        : "*" +
                                                          getDefaultPath(
                                                              s.server_id,
                                                          )}
                                                </label>
                                                <label className="text-lg text-pretty">
                                                    {s.format && s.format !== ""
                                                        ? s.format
                                                        : "*" +
                                                          getDefaultFormat(
                                                              s.server_id,
                                                          )}
                                                </label>
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                    <div className="flex justify-center flex-col w-full items-center gap-1">
                        {successText ? (
                            <div className="w-full p-4">
                                <Alert
                                    msg={{
                                        status: 200,
                                        msg: "Swap successfully done!",
                                    }}
                                />
                            </div>
                        ) : to === from &&
                          to !== undefined &&
                          from !== undefined &&
                          !successText ? (
                            <div className="w-full p-4">
                                <Alert
                                    msg={{
                                        status: 400,
                                        msg: "Swap same source station order is not possible!",
                                        errors: {
                                            errors: [
                                                {
                                                    code: "400",
                                                    detail: "",
                                                    attr: "merge",
                                                },
                                            ],
                                            type: "MergeError",
                                        } as Errors,
                                    }}
                                />
                            </div>
                        ) : null}
                        <button
                            disabled={
                                !(to && from) || to === from || successText
                            }
                            className="btn btn-neutral w-32"
                            type="submit"
                        >
                            {loading && <Spinner size="lg" />}
                            {!loading && (
                                <div className="flex justify-center items-center gap-1">
                                    <p className="font-semibold text-base">
                                        Swap
                                    </p>
                                    <ArrowRightIcon className="size-5" />
                                </div>
                            )}
                        </button>
                    </div>
                </form>
            </div>
        </Modal>
    );
};

export default StationChangeTryOrderModal;

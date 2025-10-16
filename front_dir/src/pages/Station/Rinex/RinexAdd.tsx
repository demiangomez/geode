import { useEffect, useState } from "react";
import { useDropzone } from "react-dropzone";
import { Alert, Modal, Table } from "@componentsReact";

import { CloudArrowDownIcon } from "@heroicons/react/24/outline";

import { useAuth, useApi } from "@hooks";

import { getRecordsFromFile, postStationInfoByFileService } from "@services";
import { apiOkStatuses, formattedDates, woTz } from "@utils";
import { Errors, RinexAddFile, RinexFileResponse, ErrorResponse } from "@types";

interface Props {
    stationApiId: number;
    handleCloseModal: () => void;
    setModalState: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
}

type RecordsOfFile = {
    station_info_records_on_file: Record[];
    statusCode: number;
};

type Record = {
    NetworkCode: string;
    StationCode: string;
    ReceiverCode: string;
    ReceiverSerial: string;
    ReceiverFirmware: string;
    AntennaCode: string;
    AntennaSerial: string;
    AntennaHeight: number;
    AntennaNorth: number;
    AntennaEast: number;
    HeightCode: string;
    RadomeCode: string;
    DateStart: {
        stninfo: string;
    };
    DateEnd: {
        stninfo: string;
    };
    Comments: string | null;
    ReceiverVers: string;
    hash: number;
    record_format: string;
};

type RecordToInsert = {
    NetworkCode: string;
    StationCode: string;
    DateStart: string;
};

type DropzoneProps = {
    file: File | undefined;
    setFile: React.Dispatch<React.SetStateAction<File | undefined>>;
};

const Dropzone = ({ file, setFile }: DropzoneProps) => {
    const { acceptedFiles, getRootProps, getInputProps } = useDropzone({
        maxFiles: 1,
    });

    useEffect(() => {
        if (acceptedFiles.length > 0) {
            setFile(acceptedFiles[0]);
        }
    }, [acceptedFiles, setFile]);

    return (
        <section className="w-full p-2">
            <div
                {...getRootProps({
                    className:
                        "w-full border-2 border-dashed cursor-pointer border-neutral-400 rounded-lg p-4 focus:border-violet-500 text-neutral-500",
                    style: {
                        flex: 1,
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        padding: "20px",
                        backgroundColor: "#fafafa",
                        outline: "none",
                        transition: "border .24s ease-in-out",
                    },
                })}
            >
                <input {...getInputProps()} />
                {file ? (
                    <div>
                        <p>{file.name}</p>
                        <p>{file.size} bytes</p>
                    </div>
                ) : (
                    <>
                        <CloudArrowDownIcon className="size-10" />
                        <p>
                            Drag 'n' drop station info file, or click to select
                            file
                        </p>
                    </>
                )}
            </div>
        </section>
    );
};

const RinexAdd = ({ stationApiId, handleCloseModal, setModalState }: Props) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const [loading, setLoading] = useState<boolean>(false);
    const [msg, setMsg] = useState<
        | {
              status: number;
              msg: string;
              errors?: RinexFileResponse | Errors;
              rinex_other_errors?: { [key: string]: string[] };
          }
        | undefined
    >(undefined);

    const [file, setFile] = useState<File | undefined>(undefined);

    const [records, setRecords] = useState<RecordToInsert[]>([]);

    const [tableData, setTableData] = useState<(string | number | null)[][]>();

    const titles = [
        "NET CODE",
        "ST CODE",
        "DATE START",
        "DATE END",
        "RX CODE",
        "RX SERIAL",
        "RX FW",
        "ANT CODE",
        "ANT SERIAL",
        "HEIGHT",
        "NORTH",
        "EAST",
        "HC",
        "RAD",
        "COMMENTS",
    ];

    const getRecords = async () => {
        try {
            setLoading(true);
            setMsg(undefined);

            const formData = new FormData();
            formData.append("file", file as File);

            const res = await getRecordsFromFile<RecordsOfFile | ErrorResponse>(
                api,
                stationApiId,
                formData,
            );

            if (res) {
                if ("status" in res) {
                    setMsg({
                        status: res.statusCode,
                        msg: "No Records valid",
                        errors: res.response,
                    });
                    setTableData([]);
                } else {
                    const recordsOnFile = res.station_info_records_on_file;

                    const data = recordsOnFile?.map(
                        ({
                            NetworkCode,
                            StationCode,
                            DateStart,
                            DateEnd,
                            //eslint-disable-next-line
                            ReceiverVers,
                            //eslint-disable-next-line
                            hash,
                            //eslint-disable-next-line
                            record_format,
                            ...restOfStationInfo
                        }: Record) => {
                            return [
                                NetworkCode,
                                StationCode,
                                DateStart.stninfo,
                                DateEnd.stninfo,
                                ...Object.values(restOfStationInfo),
                            ];
                        },
                    );
                    setTableData(data);
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const addAllRecords = () => {
        if (!tableData) return;

        if (records.length !== tableData.length) {
            const data = tableData
                .map((row) => {
                    const networkCode = row[0]?.toString();
                    const stationCode = row[1]?.toString();
                    const dateStart = row[2]?.toString();

                    if (
                        networkCode !== undefined &&
                        stationCode !== undefined &&
                        dateStart !== undefined
                    ) {
                        return {
                            NetworkCode: networkCode,
                            StationCode: stationCode,
                            DateStart: dateStart,
                        };
                    }
                    return null;
                })
                .filter((row): row is RecordToInsert => row !== null);

            setRecords(data);
        } else {
            setRecords([]);
        }
    };

    const addOrDiscardRecord = (row: (string | number | null)[]) => {
        if (!tableData) return;

        const networkCode = row[0] as string;
        const stationCode = row[1] as string;
        const dateStart = row[2] ? row[2].toString() : "";

        setRecords((prev = []) => {
            const exists = prev.some(
                (r) =>
                    r.NetworkCode === networkCode &&
                    r.StationCode === stationCode &&
                    r.DateStart === dateStart,
            );
            if (exists) {
                // Remove record
                return prev.filter(
                    (r) =>
                        !(
                            r.NetworkCode === networkCode &&
                            r.StationCode === stationCode &&
                            r.DateStart === dateStart
                        ),
                );
            } else {
                // Add record
                return [
                    ...prev,
                    {
                        NetworkCode: networkCode,
                        StationCode: stationCode,
                        DateStart: dateStart,
                    },
                ];
            }
        });
    };

    const addFile = async () => {
        try {
            setLoading(true);
            setMsg(undefined);
            const recordsToInsert = {
                records_to_insert: records,
            };

            const formData = new FormData();
            formData.append("file", file as File);
            formData.append(
                "records_to_insert",
                JSON.stringify(recordsToInsert),
            );

            const res = await postStationInfoByFileService<
                RinexAddFile | RinexFileResponse
            >(api, stationApiId, formData);

            if (res) {
                if ("status" in res) {
                    setMsg({
                        status: res.statusCode,
                        msg: "No station info created",
                        errors: res.response,
                    });
                } else if (
                    apiOkStatuses.includes(res.statusCode) &&
                    !("status" in res)
                ) {
                    const stationInfoInserted = res.inserted_station_info;

                    const errorMessage = res.error_message;

                    const formattedMsg = stationInfoInserted
                        .map(
                            (info) =>
                                `Station Code --> ${info.station_code?.toUpperCase()} \n Network Code --> ${info.network_code?.toUpperCase()} \n Date start --> ${formattedDates(woTz(new Date(info.date_start as string)) as Date)}`,
                        )
                        .join("\n");

                    setMsg({
                        status: res?.statusCode,
                        msg: `Station info successfully created\n${formattedMsg}`,
                        rinex_other_errors: errorMessage,
                    });
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (file != undefined) {
            getRecords();
        }
    }, [file]);

    return (
        <Modal
            close={true}
            modalId={"RinexAdd"}
            size={"smPlus"}
            setModalState={setModalState}
            handleCloseModal={handleCloseModal}
        >
            <div className="flex p-2 items-center justify-center">
                <Dropzone file={file} setFile={setFile} />
            </div>

            {tableData != undefined && tableData?.length > 0 && (
                <Table
                    titles={titles}
                    body={tableData}
                    loading={loading}
                    table={"Station"}
                    dataOnly={true}
                    multipleSelect={true}
                    onClickFunctionWithValue={addOrDiscardRecord}
                    onAlterClickFunction={() => addAllRecords()}
                    state={records}
                    setState={setRecords}
                    onClickFunction={() => {}}
                    dataFetchUrl="api/station"
                />
            )}

            <div className="w-full flex flex-col items-center mt-2">
                <button
                    className="btn btn-success w-[160px]"
                    type="button"
                    onClick={() => addFile()}
                    disabled={
                        !file ||
                        loading ||
                        apiOkStatuses.includes(Number(msg?.status)) ||
                        records.length === 0
                    }
                >
                    Add asd
                    {loading && (
                        <span className="loading loading-spinner loading-sm self-center"></span>
                    )}
                </button>
            </div>

            <div className="flex justify-center mt-4">
                {msg && <Alert msg={msg} />}
            </div>
        </Modal>
    );
};

export default RinexAdd;

import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
    Alert,
    ConfirmDeleteModal,
    DateTimePicker,
    Menu,
    MenuButton,
    MenuContent,
    Modal,
    TraceReceiverModal,
} from "@componentsReact";

import "react-datepicker/dist/react-datepicker.css";

import {
    AntennaData,
    AntennaServiceData,
    ErrorResponse,
    Errors,
    ExtendedStationInfoData,
    GamitHTCData,
    GamitHTCServiceData,
    GetParams,
    ReceiversData,
    ReceiversServiceData,
    StationInfoData,
} from "@types";

import {
    delStationInfoService,
    getAntennasService,
    getHeightCodesService,
    getReceiversService,
    postStationInfoService,
    putStationInfoService,
} from "@services";

import { useApi, useAuth, useEscape, useFormReducer } from "@hooks";
import { STATION_INFO_STATE } from "@utils/reducerFormStates";
import {
    apiOkStatuses,
    dateFromDay,
    dateToUTC,
    dayFromDate,
    formattedDates,
    showModal,
} from "@utils";
import { MagnifyingGlassIcon } from "@heroicons/react/24/outline";

interface EditStatsModalProps {
    stationInfo: StationInfoData | undefined;
    modalType: "add" | "edit" | "none";
    typeAddition: "last" | "none-clear" | undefined;
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
    setStationInfo?: React.Dispatch<
        React.SetStateAction<StationInfoData | undefined>
    >;
    setTypeAddition: React.Dispatch<
        React.SetStateAction<"last" | "none-clear" | undefined>
    >;
    reFetch: () => void;
}

const EditStatsModal = ({
    stationInfo,
    modalType,
    typeAddition,
    setTypeAddition,
    setStateModal,
    setStationInfo,
    reFetch,
}: EditStatsModalProps) => {
    const { nc, sc } = useParams();

    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const { formState, dispatch } =
        useFormReducer<Record<string, any>>(STATION_INFO_STATE);

    const [loading, setLoading] = useState<boolean>(false);
    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [receivers, setReceivers] = useState<ReceiversData[]>([]);
    const [matchingReceivers, setMatchingReceivers] = useState<ReceiversData[]>(
        [],
    );

    const [antennas, setAntennas] = useState<AntennaData[]>([]);
    const [matchingAntennas, setMatchingAntennas] = useState<AntennaData[]>([]);

    const [heightcodes, setHeightcodes] = useState<GamitHTCData[]>([]);
    const [matchingHeightcodes, setMatchingHeightcodes] = useState<
        GamitHTCData[]
    >([]);

    const [startDate, setStartDate] = useState<Date | null>(null);

    const [endDate, setEndDate] = useState<Date | null>(null);

    const [doyCheck, setDoyCheck] = useState<
        { [key: string]: { check: boolean; input: string } } | undefined
    >({
        date_start: {
            check: true,
            input: "",
        },
        date_end: { check: true, input: "" },
    });

    const [showMenu, setShowMenu] = useState<
        { type: string; show: boolean } | undefined
    >(undefined);

    useEffect(() => {
        if (stationInfo && (modalType === "edit" || modalType === "none")) {
            stationInfo.antenna_east =
                stationInfo.antenna_east !== null
                    ? stationInfo.antenna_east.toString()
                    : "";
            stationInfo.antenna_north =
                stationInfo.antenna_north !== null
                    ? stationInfo.antenna_north.toString()
                    : "";
            stationInfo.antenna_height =
                stationInfo.antenna_height !== null
                    ? stationInfo.antenna_height.toString()
                    : "";

            dispatch({
                type: "set",
                payload: stationInfo,
            });
        } else {
            dispatch({
                type: "change_value",
                payload: {
                    inputName: "network_code",
                    inputValue: nc,
                },
            });
            dispatch({
                type: "change_value",
                payload: {
                    inputName: "station_code",
                    inputValue: sc,
                },
            });
        }
    }, [stationInfo]); // eslint-disable-line

    const handleChange = (
        e:
            | React.ChangeEvent<HTMLInputElement>
            | React.MouseEvent<HTMLInputElement, MouseEvent>
            | { target: { name: string; value: string } },
    ) => {
        // Determina el tipo de evento y obtiene el target correctamente
        const target =
            (e as React.ChangeEvent<HTMLInputElement>).target ??
            (e as React.MouseEvent<HTMLInputElement>).target ??
            (e as { target: { name: string; value: string } }).target;
        const { value, name } = target as HTMLInputElement;

        if (name === "receiver_code") {
            const match = receivers.filter((receiver) =>
                receiver.receiver_code
                    .toLowerCase()
                    .includes(value.toLowerCase()),
            );
            setMatchingReceivers(match);
        }
        if (name === "antenna_code") {
            const match = antennas.filter((ant) =>
                ant.antenna_code.toLowerCase().includes(value.toLowerCase()),
            );
            setMatchingAntennas(match);
        }

        if (name === "height_code") {
            const match = heightcodes.filter((hc) =>
                hc.height_code.toLowerCase().includes(value.toLowerCase()),
            );
            setMatchingHeightcodes(match);
        }

        dispatch({
            type: "change_value",
            payload: {
                inputName: name,
                inputValue: value,
            },
        });
    };

    const getReceivers = async () => {
        try {
            const res = await getReceiversService<ReceiversServiceData>(api);
            if (res) {
                setReceivers(res.data);
            }
        } catch (error) {
            console.error(error);
        }
    };

    const getAntennas = async () => {
        try {
            const res = await getAntennasService<AntennaServiceData>(api);
            if (res) {
                setAntennas(res.data);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const getHeightCodes = async (params: GetParams) => {
        try {
            const res = await getHeightCodesService<GamitHTCServiceData>(
                api,
                params,
            );
            if (res) {
                setHeightcodes(res.data);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const postStationInfo = async () => {
        try {
            setLoading(true);

            const { date_end, ...rest } = formState;

            const res = await postStationInfoService<
                ExtendedStationInfoData | ErrorResponse
            >(api, date_end.trim() === "" ? rest : formState);

            if (res) {
                if ("status" in res) {
                    setMsg({
                        status: res.statusCode,
                        msg: res.response.type,
                        errors: res.response,
                    });
                } else {
                    setMsg({
                        status: res.statusCode,
                        msg: "Station info added successfully",
                    });
                    reFetch();
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const putStationInfo = async () => {
        try {
            setLoading(true);

            formState.date_end =
                formState.date_end?.trim() === "" || !formState.date_end
                    ? null
                    : formState.date_end;

            const res = await putStationInfoService<
                ExtendedStationInfoData | ErrorResponse
            >(api, Number(formState.api_id), formState);

            if (res) {
                if ("status" in res) {
                    setMsg({
                        status: res.statusCode,
                        msg: res.response.type,
                        errors: res.response,
                    });
                } else {
                    setMsg({
                        status: res.statusCode,
                        msg: "Station info updated successfully",
                    });
                    reFetch();
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const delStationInfo = async () => {
        try {
            setLoading(true);
            const res = await delStationInfoService<ErrorResponse>(
                api,
                Number(formState.api_id),
            );

            if (res) {
                if ("status" in res && res.status === "success") {
                    setMsg({
                        status: res.statusCode,
                        msg: res.msg,
                    });
                    reFetch();
                } else {
                    setMsg({
                        status: res.statusCode,
                        msg: res.response.type,
                        errors: res.response,
                    });
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        setMsg(undefined);
        if (modalType === "add" || modalType === "none") {
            postStationInfo();
        } else if (modalType === "edit") {
            putStationInfo();
        }
        //dispatchAndClearDoys();
    };

    useEffect(() => {
        getReceivers();
        getAntennas();
        getHeightCodes({
            limit: 0,
            offset: 0,
            antenna_code: "",
        });
    }, []); // eslint-disable-line

    useEffect(() => {
        if (formState.antenna_code) {
            getHeightCodes({
                limit: 5,
                offset: 0,
                antenna_code: formState.antenna_code,
            });
        }
    }, [formState.antenna_code]); // eslint-disable-line

    useEffect(() => {
        if (formState.date_start && formState.date_start !== null) {
            setStartDate(dateToUTC(formState.date_start));
        }
    }, [formState.date_start]);

    useEffect(() => {
        if (formState.date_end && formState.date_end !== null) {
            setEndDate(dateToUTC(formState.date_end));
        }
    }, [formState.date_end]);

    const closeModal = () => {
        setStationInfo ? setStationInfo(undefined) : null;

        STATION_INFO_STATE.network_code = nc ?? "";
        STATION_INFO_STATE.station_code = sc ?? "";

        setTypeAddition(undefined);

        dispatch({
            type: "set",
            payload: STATION_INFO_STATE,
        });
    };

    const dispatchAndClearDoys = () => {
        dispatch({ type: "clear" });
        setDoyCheck({
            date_start: {
                check: true,
                input: "",
            },
            date_end: { check: true, input: "" },
        });
    };

    const inputRefReceiverCode = useRef<HTMLInputElement>(null);

    const inputRefAntenaCode = useRef<HTMLInputElement>(null);

    const inputRefHeightCode = useRef<HTMLInputElement>(null);

    const selectRef = (key: string) => {
        return key === "receiver_code"
            ? inputRefReceiverCode
            : key === "antenna_code"
              ? inputRefAntenaCode
              : key === "height_code"
                ? inputRefHeightCode
                : null;
    };

    useEffect(() => {
        if (showMenu) {
            const ref = selectRef(showMenu.type);
            if (ref && ref.current) {
                ref.current.focus();
            }
        }
    }, [showMenu]);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    useEscape(() => {
        closeModal();
        dispatchAndClearDoys();
        setMsg(undefined);
    });

    return (
        <Modal
            close={true}
            modalId={"EditStats"}
            size={"md"}
            handleCloseModal={closeModal}
            setModalState={setStateModal}
        >
            <div>
                {typeAddition && (
                    <button
                        className="btn absolute left-6 top-6"
                        onClick={() =>
                            setModals({
                                show: true,
                                title: "TraceReceiverModal",
                                type: "none",
                            })
                        }
                    >
                        Trace Receiver{" "}
                        <MagnifyingGlassIcon className="size-5" />
                    </button>
                )}
                <h3 className="font-bold text-center text-2xl my-2 w-full">
                    {modalType === "none"
                        ? "Add"
                        : modalType.charAt(0).toUpperCase() +
                          modalType.slice(1) +
                          (modalType === "edit"
                              ? " " +
                                stationInfo?.network_code.toUpperCase() +
                                "." +
                                stationInfo?.station_code.toUpperCase()
                              : "")}
                </h3>
            </div>
            <form className="form-control space-y-4" onSubmit={handleSubmit}>
                <div className="form-control space-y-2">
                    {Object.entries(formState || {}).map(([key], index) => {
                        const inputsToDisable = [
                            "api_id",
                            "network_code",
                            "station_code",
                        ];
                        const inputsToDatePicker = ["date_start", "date_end"];
                        const errorBadge = msg?.errors?.errors?.find(
                            (error) => error.attr === key,
                        );

                        return (
                            <div className="flex flex-col" key={index}>
                                {errorBadge && (
                                    <div className="badge badge-error gap-2 self-end -mb-2 z-[1]">
                                        {errorBadge.code.toUpperCase()}
                                    </div>
                                )}
                                <div className="flex w-full">
                                    <label
                                        key={index}
                                        id={key}
                                        className={`w-full input input-bordered flex items-center 
                                            gap-2 ${errorBadge ? "input-error" : ""} 
                                            ${inputsToDatePicker.includes(key) ? "w-11/12" : ""}
                                             ${inputsToDisable.includes(key) ? "hidden" : ""}
                                            `}
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
                                            type={
                                                key in inputsToDatePicker
                                                    ? "datetime-local"
                                                    : "text"
                                            }
                                            ref={selectRef(key)}
                                            name={key}
                                            value={
                                                inputsToDatePicker.includes(key)
                                                    ? doyCheck?.[key]?.check
                                                        ? doyCheck[
                                                              key
                                                          ].input.trim() !== ""
                                                            ? doyCheck[key]
                                                                  .input
                                                            : dayFromDate(
                                                                    formState?.[
                                                                        key as keyof typeof formState
                                                                    ],
                                                                )?.trim() !== ""
                                                              ? (dayFromDate(
                                                                    formState?.[
                                                                        key as keyof typeof formState
                                                                    ],
                                                                ) ?? "")
                                                              : ""
                                                        : inputsToDatePicker.includes(
                                                                key,
                                                            ) &&
                                                            formState[
                                                                key as keyof typeof formState
                                                            ] !== "" &&
                                                            formState[
                                                                key as keyof typeof formState
                                                            ] !== null
                                                          ? formattedDates(
                                                                new Date(
                                                                    formState[
                                                                        key as keyof typeof formState
                                                                    ],
                                                                ),
                                                            )
                                                          : ""
                                                    : (formState[
                                                          key as keyof typeof formState
                                                      ] ?? "")
                                            }
                                            onChange={(e) => {
                                                const inputValue =
                                                    e.target.value;
                                                const hasDoy =
                                                    (key === "date_start" ||
                                                        key === "date_end") &&
                                                    doyCheck?.[key].check;

                                                setShowMenu({
                                                    type: key,
                                                    show: true,
                                                });

                                                if (hasDoy) {
                                                    setDoyCheck({
                                                        ...doyCheck,
                                                        [key]: {
                                                            check: true,
                                                            input:
                                                                inputValue ?? 0,
                                                        },
                                                    });

                                                    const dateValue = inputValue
                                                        ? dateFromDay(
                                                              inputValue,
                                                          )?.toISOString()
                                                        : null;

                                                    //Si la fecha no es valida setear el picker en null
                                                    if (dateValue === null) {
                                                        if (
                                                            key === "date_start"
                                                        ) {
                                                            setStartDate(null);
                                                        } else {
                                                            setEndDate(null);
                                                        }
                                                    }

                                                    dispatch({
                                                        type: "change_value",
                                                        payload: {
                                                            inputName: key,
                                                            inputValue:
                                                                dateValue ?? "",
                                                        },
                                                    });
                                                } else {
                                                    handleChange(e);
                                                }
                                            }}
                                            className={`grow ${inputsToDisable.includes(key) ? "hidden" : ""}`}
                                            autoComplete="off"
                                            // disabled={inputsToDisable.includes(
                                            //     key,
                                            // )}
                                            readOnly={
                                                inputsToDatePicker.includes(
                                                    key,
                                                ) && !doyCheck?.[key].check
                                            }
                                            placeholder={
                                                inputsToDatePicker.includes(
                                                    key,
                                                ) && doyCheck?.[key].check
                                                    ? "YYYY DDD HH MM SS"
                                                    : ""
                                            }
                                            onClick={(e) => {
                                                if (
                                                    key === "receiver_code" ||
                                                    key === "antenna_code" ||
                                                    key === "height_code"
                                                ) {
                                                    handleChange(e);
                                                    setShowMenu({
                                                        type: key,
                                                        show: true,
                                                    });
                                                }
                                            }}
                                        />
                                        {inputsToDatePicker.includes(key) &&
                                            !doyCheck?.[key].check && (
                                                <>
                                                    <DateTimePicker
                                                        typeKey={key}
                                                        startDate={startDate}
                                                        endDate={endDate}
                                                        setStartDate={
                                                            setStartDate
                                                        }
                                                        setEndDate={setEndDate}
                                                        dispatch={dispatch}
                                                    />
                                                </>
                                            )}
                                        {(key === "receiver_code" ||
                                            key === "antenna_code" ||
                                            key === "height_code") && (
                                            <MenuButton
                                                setShowMenu={setShowMenu}
                                                onMenuClick={() =>
                                                    handleChange({
                                                        target: {
                                                            name: key,
                                                            value: formState[
                                                                key
                                                            ],
                                                        },
                                                    })
                                                }
                                                showMenu={showMenu}
                                                typeKey={key}
                                            />
                                        )}
                                    </label>
                                    {inputsToDatePicker.includes(key) && (
                                        <div className="form-control justify-center w-1/12">
                                            <label className="label cursor-pointer">
                                                <span className="label-text ml-auto mr-2 text-center font-semibold">
                                                    DOY
                                                </span>
                                                <input
                                                    type="checkbox"
                                                    checked={
                                                        doyCheck?.[key].check
                                                    }
                                                    onChange={() => {
                                                        setDoyCheck({
                                                            ...doyCheck,
                                                            [key]: {
                                                                check: !doyCheck?.[
                                                                    key
                                                                ].check,
                                                                input:
                                                                    dayFromDate(
                                                                        formState?.[
                                                                            key as keyof typeof formState
                                                                        ],
                                                                    ) ?? "",
                                                            },
                                                        });
                                                    }}
                                                    className="checkbox"
                                                />
                                            </label>
                                        </div>
                                    )}
                                </div>
                                {showMenu?.show &&
                                showMenu.type === key &&
                                key === "receiver_code" ? (
                                    <Menu>
                                        {(matchingReceivers.length > 0
                                            ? matchingReceivers
                                            : receivers
                                        )?.map((receiver) => (
                                            <MenuContent
                                                key={
                                                    receiver.api_id +
                                                    receiver.receiver_code
                                                }
                                                typeKey={key}
                                                value={receiver.receiver_code}
                                                dispatch={dispatch}
                                                setShowMenu={setShowMenu}
                                            />
                                        ))}
                                    </Menu>
                                ) : showMenu?.show &&
                                  showMenu.type === key &&
                                  key === "antenna_code" ? (
                                    <Menu>
                                        {(matchingAntennas.length > 0
                                            ? matchingAntennas
                                            : antennas
                                        )?.map((ant) => (
                                            <MenuContent
                                                key={
                                                    ant.api_id +
                                                    ant.antenna_code
                                                }
                                                typeKey={key}
                                                value={ant.antenna_code}
                                                dispatch={dispatch}
                                                setShowMenu={setShowMenu}
                                            />
                                        ))}
                                    </Menu>
                                ) : (
                                    showMenu?.show &&
                                    showMenu.type === key &&
                                    key === "height_code" && (
                                        <Menu>
                                            {(matchingHeightcodes?.length > 0
                                                ? matchingHeightcodes
                                                : heightcodes
                                            )?.map((hc) => (
                                                <MenuContent
                                                    key={
                                                        hc.api_id +
                                                        hc.height_code
                                                    }
                                                    typeKey={key}
                                                    value={hc.height_code}
                                                    dispatch={dispatch}
                                                    setShowMenu={setShowMenu}
                                                />
                                            ))}
                                        </Menu>
                                    )
                                )}
                            </div>
                        );
                    })}
                </div>
                <Alert msg={msg} />
                <div className="flex w-full justify-center space-x-4">
                    <button
                        type="submit"
                        className="btn btn-success w-5/12"
                        disabled={
                            apiOkStatuses.includes(Number(msg?.status)) ||
                            loading
                        }
                    >
                        {loading && (
                            <span className="loading loading-spinner loading-md"></span>
                        )}
                        Submit
                    </button>

                    {typeAddition === "last" && (
                        <a
                            className="link-hover cursor-pointer"
                            style={{ marginTop: "10px", marginLeft: "10px" }}
                            onClick={dispatchAndClearDoys}
                        >
                            Clear
                        </a>
                    )}

                    {modalType === "edit" && (
                        <button
                            type="button"
                            className="btn btn-error w-3/12"
                            disabled={apiOkStatuses.includes(
                                Number(msg?.status),
                            )}
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
                    {modals && modals?.title === "ConfirmDelete" && (
                        <ConfirmDeleteModal
                            msg={msg}
                            loading={loading}
                            confirmRemove={() => delStationInfo()}
                            closeModal={() => {
                                setModals({
                                    show: false,
                                    title: "",
                                    type: "edit",
                                });
                            }}
                        />
                    )}
                </div>
            </form>
            <form>
                {modals && modals?.title === "TraceReceiverModal" && (
                    <TraceReceiverModal
                        parentReceiverType={formState.receiver_code ?? ""}
                        parentReceiverSerial={formState.receiver_serial ?? ""}
                        closeModal={() => {
                            setModals({
                                show: false,
                                title: "",
                                type: "none",
                            });
                        }}
                        parentDispatch={dispatch}
                    />
                )}
            </form>
        </Modal>
    );
};

export default EditStatsModal;

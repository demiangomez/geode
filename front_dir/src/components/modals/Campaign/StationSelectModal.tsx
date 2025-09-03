import { Link, useNavigate } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import {
    Menu,
    MenuButton,
    MenuContent,
    Modal,
    Spinner,
} from "@componentsReact";

import { getStationsService, getStationVisitsService } from "@services";

import { useFormReducer, useAuth, useApi } from "@hooks";

import {
    CampaignsData,
    StationData,
    StationServiceData,
    StationVisitsData,
    StationVisitsServiceData,
} from "@types";

interface Props {
    campaign: CampaignsData | undefined;
    setCampaign: React.Dispatch<
        React.SetStateAction<CampaignsData | undefined>
    >;
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
}

const StationSelectModal = ({
    campaign,
    setCampaign,
    setStateModal,
}: Props) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const navigate = useNavigate();

    const [showMenu, setShowMenu] = useState<
        { type: string; show: boolean } | undefined
    >(undefined);

    const [station, setStation] = useState<StationData | undefined>(undefined);

    const [stations, setStations] = useState<StationData[] | undefined>(
        undefined,
    );
    const [matchStation, setMatchStation] = useState<StationData[] | undefined>(
        undefined,
    );

    const [visits, setVisits] = useState<StationVisitsData[] | undefined>(
        undefined,
    );

    const [loading, setLoading] = useState<boolean>(true);

    const [tab, setTab] = useState<number>(1);

    const { formState, dispatch } = useFormReducer({
        name: "",
    });

    const getStation = async () => {
        try {
            setLoading(true);
            const res = await getStationsService<StationServiceData>(api, {
                limit: 0,
                offset: 0,
            });
            setStations(res.data);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const getVisits = async () => {
        try {
            const res = await getStationVisitsService<StationVisitsServiceData>(
                api,
                {
                    limit: 0,
                    offset: 0,
                    station_api_id: String(station?.api_id),
                },
            );

            if (res.statusCode === 200) {
                setVisits(res.data);
            }
        } catch (error) {
            console.error(error);
        }
    };

    useEffect(() => {
        if (station) {
            getVisits();
        }
    }, [station]);

    useEffect(() => {
        setStation(undefined);
        setVisits(undefined);
        dispatch({
            type: "change_value",
            payload: {
                inputName: "name",
                inputValue: "",
            },
        });
    }, [tab]);

    useEffect(() => {
        getStation();
    }, []);

    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (showMenu) {
            inputRef.current?.focus();
        }
    }, [showMenu]);

    return (
        <Modal
            close={false}
            modalId={"SelectStation"}
            size={"sm"}
            handleCloseModal={() => setCampaign(undefined)}
            setModalState={setStateModal}
        >
            <div className="flex p-4 flex-col">
                <div role="tablist" className="tabs tabs-bordered mb-6">
                    <a
                        role="tab"
                        onClick={() => setTab(1)}
                        className={`tab ${tab === 1 && "tab-active font-bold"} `}
                    >
                        Add new visit
                    </a>
                    <a
                        role="tab"
                        onClick={() => setTab(2)}
                        className={`tab ${tab === 2 && "tab-active font-bold"}`}
                    >
                        Add existing visit
                    </a>
                </div>

                {loading ? (
                    <div className="w-full flex justify-center items-center">
                        <Spinner size={"lg"} />
                    </div>
                ) : tab === 1 ? (
                    <>
                        <label
                            className={`w-full input input-bordered flex items-center gap-2`}
                            title={"Stations"}
                        >
                            <div className="label ">
                                <span className="font-bold">STATIONS</span>
                            </div>
                            <input
                                type="text"
                                value={formState["name"] ?? ""}
                                onChange={(e) => {
                                    const value = e.target.value;
                                    dispatch({
                                        type: "change_value",
                                        payload: {
                                            inputName: "name",
                                            inputValue: value,
                                        },
                                    });
                                    const parts = value
                                        .toLowerCase()
                                        .split(" ");
                                    const match = stations?.filter((p) =>
                                        parts.every(
                                            (part) =>
                                                p.network_code
                                                    .toLowerCase()
                                                    .includes(part) ||
                                                p.station_code
                                                    .toLowerCase()
                                                    .includes(part),
                                        ),
                                    );

                                    setMatchStation(match);
                                }}
                                ref={inputRef}
                                className="grow"
                                autoComplete="off"
                            />

                            <MenuButton
                                setShowMenu={setShowMenu}
                                showMenu={showMenu}
                                typeKey={"name"}
                            />
                        </label>
                        {showMenu?.show && showMenu?.type === "name" ? (
                            <Menu>
                                {(matchStation && matchStation.length > 0
                                    ? matchStation
                                    : stations
                                )?.map((p) => (
                                    <MenuContent
                                        key={p.api_id}
                                        typeKey={""}
                                        value={
                                            p.network_code
                                                .trim()
                                                .toUpperCase() +
                                            "." +
                                            p.station_code.trim().toUpperCase()
                                        }
                                        alterFunction={() => {
                                            navigate(
                                                `/${p.network_code}/${p.station_code}/visits`,
                                                { state: campaign },
                                            );
                                        }}
                                        setShowMenu={setShowMenu}
                                    />
                                ))}
                            </Menu>
                        ) : null}
                    </>
                ) : (
                    tab === 2 && (
                        <>
                            <label
                                className={`w-full input input-bordered flex items-center gap-2`}
                                title={"Stations"}
                            >
                                <div className="label ">
                                    <span className="font-bold">STATIONS</span>
                                </div>
                                <input
                                    type="text"
                                    value={formState["name"] ?? ""}
                                    onChange={(e) => {
                                        const value = e.target.value;
                                        dispatch({
                                            type: "change_value",
                                            payload: {
                                                inputName: "name",
                                                inputValue: value,
                                            },
                                        });
                                        const parts = value
                                            .toLowerCase()
                                            .split(" ");
                                        const match = stations?.filter((p) =>
                                            parts.every(
                                                (part) =>
                                                    p.network_code
                                                        .toLowerCase()
                                                        .includes(part) ||
                                                    p.station_code
                                                        .toLowerCase()
                                                        .includes(part),
                                            ),
                                        );

                                        setMatchStation(match);
                                    }}
                                    className="grow"
                                    autoComplete="off"
                                />

                                <MenuButton
                                    setShowMenu={setShowMenu}
                                    showMenu={showMenu}
                                    typeKey={"name"}
                                />
                            </label>
                            {showMenu?.show && showMenu?.type === "name" ? (
                                <Menu>
                                    {(matchStation && matchStation.length > 0
                                        ? matchStation
                                        : stations
                                    )?.map((p) => (
                                        <MenuContent
                                            key={p.api_id}
                                            typeKey={""}
                                            value={
                                                p.network_code
                                                    .trim()
                                                    .toUpperCase() +
                                                "." +
                                                p.station_code
                                                    .trim()
                                                    .toUpperCase()
                                            }
                                            alterFunction={() => {
                                                setStation(p);
                                                dispatch({
                                                    type: "change_value",
                                                    payload: {
                                                        inputName: "name",
                                                        inputValue:
                                                            p.network_code +
                                                            "." +
                                                            p.station_code,
                                                    },
                                                });
                                            }}
                                            setShowMenu={setShowMenu}
                                        />
                                    ))}
                                </Menu>
                            ) : null}
                            <div className="flex flex-grow flex-col justify-start items-center">
                                {visits && visits.length > 0 ? (
                                    <>
                                        <ul className="menu bg-base-200 mt-4 rounded-box w-full max-h-56 overflow-y-auto">
                                            <li>
                                                <h2 className="menu-title">
                                                    Visits
                                                </h2>
                                                <ul>
                                                    {visits.map((v) => {
                                                        return (
                                                            <li
                                                                key={v.id}
                                                                className="w-full flex"
                                                            >
                                                                <Link
                                                                    to={`/${station?.network_code}/${station?.station_code}/visits`}
                                                                    state={{
                                                                        visitDetail:
                                                                            v,
                                                                    }}
                                                                    className="font-bold text-lg"
                                                                >
                                                                    {v.date}
                                                                </Link>
                                                            </li>
                                                        );
                                                    })}
                                                </ul>
                                            </li>
                                        </ul>
                                    </>
                                ) : station ? (
                                    <span className="mt-4 font-bold text-2xl">
                                        No visits for this station
                                    </span>
                                ) : (
                                    <span className="mt-4 font-bold text-2xl">
                                        Select a station
                                    </span>
                                )}
                            </div>
                        </>
                    )
                )}
            </div>
        </Modal>
    );
};

export default StationSelectModal;

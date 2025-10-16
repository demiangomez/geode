/* eslint-disable @typescript-eslint/no-unused-vars */

import { useOutletContext } from "react-router-dom";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Rnd } from "react-rnd";

import {
    CardContainer,
    Pagination,
    RinexAddModal,
    RinexExtendModal,
    RinexFilter,
    RinexTable,
    StationInfoModal,
    StatsModal,
    TableCard,
    RinexCompletionPlot,
} from "@componentsReact";

import {
    BookmarkIcon,
    CheckCircleIcon,
    ExclamationCircleIcon,
    ExclamationTriangleIcon,
    FunnelIcon,
    QuestionMarkCircleIcon,
    XMarkIcon,
} from "@heroicons/react/24/outline";

import { useAuth, useApi } from "@hooks";

import { getRinexWithStatusService, getCompletionPlotService } from "@services";

import { ensureEndsWithZ, showModal } from "@utils";
import { RINEX_FILTERS_STATE } from "@utils/reducerFormStates";

import {
    GetParams,
    RinexData,
    RinexItem,
    RinexObject,
    RinexRelatedStationInfo,
    StationData,
    StationInfoData,
    CompletionPlotServiceData,
} from "@types";

type OutletContext = {
    station: StationData;
    reStation: StationData;
    getReStation: () => void;
};

type CloseFunction = () => void;

const Actions = ({ close }: { close: CloseFunction }) => {
    return (
        <div className="grid grid-cols-1 grid-flow-dense gap-3 relative ">
            <button
                type="button"
                onClick={() => close()}
                className="justify-self-end"
            >
                <XMarkIcon className="size-5" />
            </button>
            <div className="grid grid-cols-2 gap-3 grid-flow-dense text-lg w-full">
                <div className=" card border-[1px] p-4 border-neutral-300 flex flex-col space-y-2">
                    <span className="w-full flex justify-center font-bold text-xl">
                        Actions
                    </span>
                    <div className="flex flex-col break-words justify-center h-full space-y-4">
                        <span>
                            <strong>V</strong> = View (opens station information
                            record to view)
                        </span>
                        <span>
                            <strong>E</strong> = Edit
                        </span>
                        <span>
                            <strong>↧ ↥</strong> = Pull station info down/up to
                            cover
                        </span>
                        <span>
                            <strong>+</strong> = Add station information using
                            RINEX metadata
                        </span>
                    </div>
                </div>
                <div className="space-y-2 card border-[1px] p-4 border-neutral-300 flex flex-col">
                    <span className="font-bold text-xl flex justify-center ">
                        Status
                    </span>
                    <div className="flex flex-col space-y-4">
                        <div className="flex flex-col break-words items-center justify-center">
                            <ExclamationTriangleIcon className="size-7 fill-yellow-400" />{" "}
                            <span className="font-medium text-center">
                                Mismatch between RINEX metadata and station
                                information (see red-underlined fields)
                            </span>
                        </div>
                        <div className="flex flex-col break-words items-center justify-center">
                            <ExclamationCircleIcon className="size-7 fill-red-600" />

                            <span className="font-medium text-center">
                                Missing station information record
                            </span>
                        </div>
                        <div className="flex flex-col break-words items-center justify-center">
                            <CheckCircleIcon className="size-7 fill-green-500 " />
                            <span className="font-medium text-center">
                                Station information ok
                            </span>
                        </div>

                        <div className="flex flex-col break-words items-center justify-center">
                            <QuestionMarkCircleIcon className="size-7 fill-gray-300" />

                            <span className="font-medium text-center">
                                Multiple station info records
                            </span>
                        </div>
                    </div>
                </div>
            </div>
            <div className="grid grid-cols-1 card p-4 border-[1px] space-y-2 items-center border-neutral-300  w-full">
                <span className="w-full flex justify-center font-bold text-xl">
                    Background color
                </span>
                <span>
                    <strong>RED</strong> ⇾ No station info
                </span>
                <span>
                    <strong>LIGHT RED</strong> ⇾ No station info but completion
                    &lt; 0.5
                </span>
                <span>
                    <strong>GRAY</strong> ⇾ Station info but completion &lt; 0.5
                </span>
                <span>
                    <strong>GREEN</strong> ⇾ All good
                </span>
            </div>
        </div>
    );
};

const Rinex = () => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const { station, getReStation } = useOutletContext<OutletContext>();

    const [lastGroupIdPreviousPage, setLastGroupIdPreviousPage] = useState<
        string | undefined
    >(undefined);

    const [sameGroup, setSameGroup] = useState<boolean>(false);

    const [actionsManual, setActionsManual] = useState<boolean>(false);

    const [rinexCheckbox, setRinexCheckbox] = useState<boolean>(false);

    const [rinexFilter, setRinexFilter] = useState<boolean>(false);

    const [filters, setFilters] =
        useState<Record<keyof typeof RINEX_FILTERS_STATE, any>>(
            RINEX_FILTERS_STATE,
        );

    const [operatorSelected, setOperatorSelected] = useState<string>("<"); // eslint-disable-line

    const [rinexFilterData, setRinexFilterData] = useState<
        RinexObject[] | undefined
    >(undefined);

    const [rinex, setRinex] = useState<RinexObject[] | undefined>(undefined);

    const [shouldFetchRinex, setShouldFetchRinex] = useState<boolean>(false);

    // BUTTON FIRST LEVEL
    const [rinexStationInfoRelated, setRinexStationInfoRelated] = useState<
        RinexRelatedStationInfo[] | undefined
    >(undefined);

    const [rinexGroup, setRinexGroup] = useState<RinexItem[] | undefined>(
        undefined,
    );

    // BUTTONS EXTEND RINEX SECOND LEVEL
    const [singleRinex, setSingleRinex] = useState<RinexData | undefined>(
        undefined,
    );

    const [extendTypeRinex, setExtendTypeRinex] = useState<
        "up" | "down" | undefined
    >(undefined);

    const [plotData, setPlotData] = useState<string>("");

    const [problematicRinex, setProblematicRinex] = useState<
        RinexObject[] | undefined
    >(undefined);

    const [paginatedRinexs, setPaginatedRinexs] = useState<
        RinexObject[] | undefined
    >(undefined);

    const [showCompletionPlot, setShowCompletionPlot] =
        useState<boolean>(false);

    const [loading, setLoading] = useState<boolean>(false);

    const formattedTitles = (rinexs: RinexObject[]) => {
        const titles = rinexs?.[0]?.rinex[0]?.rinex[0] || {};

        const {
            has_station_info,
            has_multiple_station_info_gap,
            metadata_mismatch,
            gap_type,
            network_code,
            station_code,
            api_id,
            filtered,
            ...rest
        } = titles;

        const formattedTitles = Object.keys(rest).map((title) => {
            if (title.includes("observation")) {
                title = title.split("observation_").pop() || title;
            }
            if (title.includes("receiver"))
                title = title.replace("receiver_", "rx_");
            if (title.includes("antenna"))
                title = title.replace("antenna_", "ant_");
            if (title.includes("ant_offset"))
                title = title.replace("ant_offset", "height");
            return title.replace(/_/g, " ");
        });

        return formattedTitles;
    };

    const formatDateTime = (dateTime: string): string => {
        const [date, time] = dateTime.split("T");
        if (!date || !time) return "";
        return `${date} ${time}`;
    };

    const titles = formattedTitles(rinex ?? []);

    const REGISTERS_PER_PAGE = 15;

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [activePage, setActivePage] = useState<number>(1);
    const [pages, setPages] = useState<number>(0);
    const PAGES_TO_SHOW = 2;

    const getRinex = async () => {
        try {
            setLoading(true);
            const res = await getRinexWithStatusService<RinexObject[]>(
                api,
                station.api_id ?? 0,
            );
            const rinexWithGroupId = res.map((item, index) => ({
                ...item,
                groupId: `group-${index}`,
            }));

            setRinex(rinexWithGroupId);
        } catch (err) {
            console.error(err);
        } finally {
            // !rinexFilter && setLoading(false);
            setLoading(false);
        }
    };

    const getCompletionPlot = async () => {
        try {
            const res =
                await getCompletionPlotService<CompletionPlotServiceData>(
                    api,
                    station.api_id ?? 0,
                );
            setPlotData(res.completion_plot);
        } catch (e) {
            console.error(e);
        }
    };

    const getRinexFiltered = async (
        filtersObj: Record<keyof typeof RINEX_FILTERS_STATE, any>,
    ) => {
        try {
            const params: GetParams = {
                observation_doy: filtersObj.doy,
                observation_f_year: filtersObj["f_year"],
                observation_s_time_since: formatDateTime(
                    filtersObj["s_time"] ?? "",
                ),
                observation_e_time_until: formatDateTime(
                    filtersObj["e_time"] ?? "",
                ),
                observation_year: filtersObj.year,
                antenna_dome: filtersObj["antenna_dome"],
                antenna_offset: filtersObj["antenna_offset"],
                antenna_serial: filtersObj["antenna_serial"],
                antenna_type: filtersObj["antenna_type"],
                receiver_fw: filtersObj["receiver_fw"],
                receiver_serial: filtersObj["receiver_serial"],
                receiver_type: filtersObj["receiver_type"],
                completion_operator:
                    operatorSelected === "<"
                        ? "LESS_THAN"
                        : operatorSelected === ">"
                          ? "GREATER_THAN"
                          : "EQUAL",
                completion: filtersObj.completion,
                interval: filtersObj.interval,
                offset: 0,
                limit: REGISTERS_PER_PAGE,
            };

            Object.keys(params).forEach((key) => {
                if (
                    params[key as keyof GetParams] === undefined ||
                    params[key as keyof GetParams] === ""
                ) {
                    delete params[key as keyof GetParams];
                }
            });

            setLoading(true);
            const res = await getRinexWithStatusService<RinexObject[]>(
                api,
                station.api_id ?? 0,
                params,
            );
            const rinexWithGroupId = res
                .map((item, index) => {
                    return {
                        ...item,
                        rinex: item.rinex
                            .map((r) => ({
                                ...r,
                                rinex: r.rinex.filter((r2) => !r2.filtered),
                            }))
                            .filter((r) => r.rinex.length > 0),
                        groupId: `group-${index}`,
                    };
                })
                .filter((item) => item.rinex.length > 0);

            setRinexFilterData(rinexWithGroupId);

            setActivePage(1); // FAL: 15-09-2025 Reset to first page when applying a new filter to prevent errors on pagination

            setRinexFilter(true);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const calculateProblematicRinex = (rinexs: RinexObject[]) => {
        return rinexs
            .filter((rinex) =>
                rinex.rinex.some((r) =>
                    r.rinex.some((r2) => !r2.has_station_info),
                ),
            )
            .map((rinex) => ({
                ...rinex,
                rinex: rinex.rinex
                    .map((r) => ({
                        ...r,
                        rinex: r.rinex.filter((r2) => !r2.has_station_info),
                    }))
                    .filter((r) => r.rinex.length > 0),
            }))
            .filter((rinex) => rinex.rinex.length > 0);
    };

    const calculateTotalLength = (rinexs: RinexObject[]) => {
        return rinexs.reduce((total, item) => total + item.rinex.length, 0);
    };

    const paginateRinex = (rinexs: RinexObject[], page: number) => {
        const startIndex = (page - 1) * REGISTERS_PER_PAGE;
        const endIndex = startIndex + REGISTERS_PER_PAGE;
        let currentLength = 0;
        const paginated: RinexObject[] = [];
        let firstGroupId = null;
        let lastGroupId = null;

        for (let i = 0; i < rinexs.length; i++) {
            const item = rinexs[i];
            const itemLength = item.rinex.length;

            if (currentLength + itemLength > startIndex) {
                const start = Math.max(0, startIndex - currentLength);
                const end = Math.min(itemLength, endIndex - currentLength);
                const slicedRinex = item.rinex.slice(start, end);

                const groupedRinex = slicedRinex.map((rinexItem) => ({
                    ...rinexItem,
                    groupId: item.groupId,
                }));

                paginated.push({
                    ...item,
                    rinex: groupedRinex,
                });

                if (firstGroupId === null) {
                    firstGroupId = item.groupId;
                }

                if (currentLength + itemLength >= endIndex) {
                    lastGroupId = item.groupId;
                    break;
                }
            }

            currentLength += itemLength;
        }

        return { paginated, firstGroupId, lastGroupId };
    };

    const handlePage = (page: number) => {
        if (page < 1 || page > pages) return;

        let dataToPaginate: RinexObject[] = [];

        if (rinexCheckbox && rinexFilterData) {
            dataToPaginate = problematicRinex ?? [];
        } else if (rinexCheckbox) {
            dataToPaginate = problematicRinex ?? [];
        } else if (rinexFilterData) {
            dataToPaginate = rinexFilterData;
        } else {
            dataToPaginate = rinex ?? [];
        }

        const { paginated, firstGroupId, lastGroupId } = paginateRinex(
            dataToPaginate,
            page,
        );

        setActivePage(page);
        setPaginatedRinexs(paginated);

        if (page === 1) {
            setSameGroup(false);
        }

        if (page > activePage) {
            // Navegando hacia adelante
            if (
                lastGroupIdPreviousPage !== null &&
                firstGroupId === lastGroupIdPreviousPage
            ) {
                setSameGroup(true);
            } else {
                setSameGroup(false);
            }
        } else if (page !== 1) {
            // Navegando hacia atrás
            const { lastGroupId: lastGroupIdPreviousPage } = paginateRinex(
                dataToPaginate,
                page - 1,
            );
            if (
                lastGroupIdPreviousPage !== null &&
                firstGroupId === lastGroupIdPreviousPage
            ) {
                setSameGroup(true);
            } else {
                setSameGroup(false);
            }
        }

        setLastGroupIdPreviousPage(lastGroupId ?? "");
        setRinexStationInfoRelated(undefined);
    };

    const handleRinexData = () => {
        let dataToPaginate: RinexObject[] = [];

        if (rinexCheckbox && rinexFilterData) {
            const problematic = calculateProblematicRinex(
                rinexFilterData ?? [],
            );
            setProblematicRinex(problematic);
            dataToPaginate = problematic ?? [];
        } else if (rinexCheckbox && !rinexFilterData) {
            const problematic = calculateProblematicRinex(rinex ?? []);
            setProblematicRinex(problematic);
            dataToPaginate = problematic ?? [];
        } else if (rinexFilterData) {
            dataToPaginate = rinexFilterData;
        } else {
            dataToPaginate = rinex ?? [];
        }

        const pagesCalculated = Math.ceil(
            calculateTotalLength(dataToPaginate) / REGISTERS_PER_PAGE,
        );

        const isActivePageValid = activePage <= pagesCalculated;

        const { paginated, lastGroupId } = paginateRinex(
            dataToPaginate,
            isActivePageValid ? activePage : pagesCalculated,
        );

        setPaginatedRinexs(paginated);
        setActivePage(isActivePageValid ? activePage : pagesCalculated);
        setLastGroupIdPreviousPage(lastGroupId ?? "");
        setPages(
            Math.ceil(
                calculateTotalLength(dataToPaginate) / REGISTERS_PER_PAGE,
            ),
        );
    };

    useEffect(() => {
        if (station) {
            getRinex();
        }
    }, [station]); // eslint-disable-line

    useEffect(() => {
        getCompletionPlot();
    }, []);

    useEffect(() => {
        if (rinex && !shouldFetchRinex) {
            handleRinexData();
        }
    }, [rinex, rinexCheckbox, rinexFilterData, activePage, shouldFetchRinex]); // eslint-disable-line

    useEffect(() => {
        const fetchRinex = async () => {
            if (shouldFetchRinex) {
                await getRinex();
                setShouldFetchRinex(false);
            }
        };

        fetchRinex();
    }, [shouldFetchRinex]); // eslint-disable-line

    useEffect(() => {
        setActivePage(1);
        setSameGroup(false);
    }, [rinexCheckbox, rinexFilter]);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    const rndOptions = {
        x: window.innerWidth / 2 - 370,
        y: window.innerHeight / 2 - 470,
        width: 620,
        height: 620,
    };

    const useStationInfoByRinexGroup = (
        rinexGroup: RinexItem[] | RinexData,
    ) => {
        return useMemo(() => {
            if (!rinexGroup) return {} as StationInfoData;
            // Verificamos si es un array
            if (Array.isArray(rinexGroup)) {
                const rinexGroupLastIndex = rinexGroup.length - 1;
                const rinexGroupLast = rinexGroup[rinexGroupLastIndex];
                const subFirstRinex = rinexGroup[0]?.rinex[0];
                const subLastRinex =
                    rinexGroupLast?.rinex[rinexGroupLast?.rinex.length - 1];

                return {
                    api_id: subFirstRinex?.api_id ?? 0,
                    network_code: subFirstRinex?.network_code ?? "",
                    station_code: subFirstRinex?.station_code ?? "",
                    receiver_code: subFirstRinex?.receiver_type ?? "",
                    receiver_serial: subFirstRinex?.receiver_serial ?? "",
                    receiver_firmware: subFirstRinex?.receiver_fw ?? "",
                    antenna_code: subFirstRinex?.antenna_type ?? "",
                    antenna_serial: subFirstRinex?.antenna_serial ?? "",
                    antenna_height: String(subFirstRinex?.antenna_offset ?? ""),
                    antenna_north: "0",
                    antenna_east: "0",
                    height_code: "DHARP",
                    radome_code: subFirstRinex?.antenna_dome,
                    date_start: ensureEndsWithZ(
                        subFirstRinex?.observation_s_time ?? "",
                    ),
                    date_end: ensureEndsWithZ(
                        subLastRinex?.observation_e_time ?? "",
                    ),
                    receiver_vers: "",
                    comments:
                        "Record created based on file " +
                        subFirstRinex?.filename,
                } as StationInfoData;
            } else {
                // Si no es un array (RinexData), retornamos lo necesario según la estructura de RinexData
                return {
                    api_id: rinexGroup.api_id ?? 0,
                    network_code: rinexGroup.network_code ?? "",
                    station_code: rinexGroup.station_code ?? "",
                    receiver_code: rinexGroup.receiver_type ?? "",
                    receiver_serial: rinexGroup.receiver_serial ?? "",
                    receiver_firmware: rinexGroup.receiver_fw ?? "",
                    antenna_code: rinexGroup.antenna_type ?? "",
                    antenna_serial: rinexGroup.antenna_serial ?? "",
                    antenna_height: String(rinexGroup.antenna_offset ?? ""),
                    antenna_north: "0",
                    antenna_east: "0",
                    height_code: "DHARP",
                    radome_code: rinexGroup.antenna_dome,
                    date_start: ensureEndsWithZ(
                        rinexGroup.observation_s_time ?? "",
                    ),
                    date_end: ensureEndsWithZ(
                        rinexGroup.observation_e_time ?? "",
                    ),
                    receiver_vers: "",
                    comments:
                        "Record created based on file " + rinexGroup.filename,
                } as StationInfoData;
            }
        }, [rinexGroup]);
    };

    const stationInfoByRinex = useStationInfoByRinexGroup(
        rinexGroup ?? singleRinex ?? [],
    );

    // FAL: 16-04-2025 Sidebar observer to not use show state

    const [sidebarWidth, setSidebarWidth] = useState<number>();

    const onResize = useCallback<ResizeObserverCallback>((entries) => {
        const [entry] = entries;
        if (entry) {
            setSidebarWidth(entry.contentRect.width);
        }
    }, []);

    // Set up resize observer for the sidebar
    useEffect(() => {
        const resizeObserver = new ResizeObserver(onResize);
        const sidebar = document.querySelector(".sidebar") as HTMLElement;

        if (sidebar) {
            resizeObserver.observe(sidebar);
        }

        return () => {
            if (sidebar) {
                resizeObserver.unobserve(sidebar);
            }
            resizeObserver.disconnect();
        };
    }, [onResize]);

    const containerWidth = useMemo(() => {
        const defaultW = "w-[calc(100vw-10rem)]";
        const maxW = "w-[calc(100vw-20rem)]";
        const sidebarElement = document.querySelector(".sidebar");
        if (!sidebarElement) return defaultW;

        if (sidebarElement.classList.contains("w-32")) {
            return defaultW;
        } else if (sidebarElement.classList.contains("w-72")) {
            return maxW;
        }
        return defaultW;
    }, [sidebarWidth]);

    return (
        <div className="flex flex-col items-center justify-center">
            {/* Manual actions panel */}
            {actionsManual && (
                <Rnd
                    default={rndOptions}
                    minWidth={620}
                    minHeight={400}
                    maxHeight={640}
                    bounds={"window"}
                    className="z-[1000] card border-[1px] border-neutral-300 shadow-2xl bg-base-100 p-4 overflow-y-auto scrollbar-base overflow-x-hidden"
                >
                    <Actions close={() => setActionsManual(false)} />
                </Rnd>
            )}

            <h1 className="text-2xl font-base text-center">RINEX</h1>
            <div
                className={`flex justify-center pr-2 px-2 pb-4 transition-all duration-100 ${containerWidth}`}
            >
                <CardContainer title="" titlePosition="start">
                    <TableCard title={""} size="100%">
                        <div className="w-full flex">
                            <div className="w-2/4 flex justify-start">
                                <button
                                    className="hover:scale-110 transition-all duration-200"
                                    onClick={() =>
                                        setActionsManual(!actionsManual)
                                    }
                                >
                                    <BookmarkIcon className="size-6" />
                                </button>
                            </div>
                            <div className="w-2/4 flex justify-end space-x-2">
                                <label className="label cursor-pointer space-x-4">
                                    <span className="label-text">
                                        Completion Plot
                                    </span>
                                    <input
                                        type="checkbox"
                                        className="checkbox"
                                        checked={showCompletionPlot}
                                        onChange={(e) =>
                                            setShowCompletionPlot(
                                                e.target.checked,
                                            )
                                        }
                                    />
                                </label>
                                <label className="label cursor-pointer space-x-4">
                                    <span className="label-text">
                                        Rinex with problems
                                    </span>
                                    <input
                                        type="checkbox"
                                        className="checkbox"
                                        checked={rinexCheckbox}
                                        onChange={(e) =>
                                            setRinexCheckbox(e.target.checked)
                                        }
                                    />
                                </label>
                                <div className="relative h-12">
                                    <button
                                        className="btn self-end"
                                        onClick={() =>
                                            setModals({
                                                show: true,
                                                title: "RinexFilters",
                                                type: "none",
                                            })
                                        }
                                    >
                                        Filter
                                        <FunnelIcon className="size-6" />
                                    </button>
                                    {rinexFilter && (
                                        <button
                                            className="btn btn-error btn-circle absolute left-[85px]"
                                            style={{
                                                width: "25px",
                                                height: "25px",
                                                minHeight: "10px",
                                            }}
                                            onClick={() => {
                                                setRinexFilter(false);
                                                setRinexFilterData(undefined);
                                                setFilters(RINEX_FILTERS_STATE);
                                                setShouldFetchRinex(true); // Set the flag to fetch Rinex data
                                            }}
                                        >
                                            <XMarkIcon className="size-5" />
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>

                        <RinexTable
                            titles={
                                paginatedRinexs && paginatedRinexs.length > 0
                                    ? titles
                                    : []
                            }
                            loading={loading}
                            sameGroup={sameGroup}
                            fullData={rinex ?? []}
                            data={paginatedRinexs ?? []}
                            setModals={setModals}
                            setRinexStationInfoRelated={
                                setRinexStationInfoRelated
                            }
                            setRinexGroup={setRinexGroup}
                            setSingleRinex={setSingleRinex}
                            setExtendTypeRinex={setExtendTypeRinex}
                        />
                        {paginatedRinexs && paginatedRinexs?.length > 0 ? (
                            <Pagination
                                pages={pages}
                                pagesToShow={PAGES_TO_SHOW}
                                activePage={activePage}
                                handlePage={handlePage}
                            />
                        ) : null}
                        {showCompletionPlot && (
                            <RinexCompletionPlot
                                img={`data:image/png;base64,${plotData}`}
                            />
                        )}
                    </TableCard>
                </CardContainer>
            </div>
            {modals?.show && modals.title === "RinexFilters" && (
                <RinexFilter
                    filters={filters}
                    setFilters={setFilters}
                    setRinex={setRinexFilterData}
                    setRinexFilter={setRinexFilter}
                    setStateModal={setModals}
                    setOperatorSelected={setOperatorSelected}
                    getRinexFiltered={getRinexFiltered}
                    handleCloseModal={() => undefined}
                />
            )}

            {modals?.show && modals.title === "RinexAdd" && (
                <RinexAddModal
                    stationApiId={station.api_id ?? 0}
                    setModalState={setModals}
                    handleCloseModal={() => {
                        rinexFilter ? getRinexFiltered(filters) : getRinex();
                        getReStation();
                    }}
                />
            )}

            {modals?.show && modals.title === "RinexExtend" && (
                <RinexExtendModal
                    extendType={extendTypeRinex}
                    rinex={singleRinex}
                    closeModal={() => {
                        setModals({ show: false, title: "", type: "none" });
                        rinexFilter ? getRinexFiltered(filters) : getRinex();
                        getReStation();
                        // setActivePage(1);
                        // getRinex();
                    }}
                    handleCloseModal={() => {
                        rinexFilter ? getRinexFiltered(filters) : getRinex();
                        getReStation();

                        // setActivePage(1);
                    }}
                    setModalState={setModals}
                />
            )}
            {modals?.show && modals.title === "Information" && (
                <StationInfoModal
                    close={false}
                    station={station}
                    size={"xl"}
                    rinexStationInfo={rinexStationInfoRelated ?? undefined}
                    setModalState={setModals}
                    refetch={() => {
                        setRinexStationInfoRelated(undefined);
                        // setActivePage(1);
                        rinexFilter ? getRinexFiltered(filters) : getRinex();
                        getReStation();
                    }}
                />
            )}

            {modals?.show && modals?.title === "EditStats" && (
                <StatsModal
                    stationInfo={stationInfoByRinex}
                    modalType={modals.type}
                    typeAddition={"none-clear"}
                    setTypeAddition={() => undefined}
                    reFetch={() => {
                        // setActivePage(1);
                        rinexFilter ? getRinexFiltered(filters) : getRinex();
                        getReStation();
                        setSingleRinex(undefined);
                        setRinexGroup(undefined);
                    }}
                    setStateModal={setModals}
                />
            )}
        </div>
    );
};

export default Rinex;

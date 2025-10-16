import { useEffect, useMemo, useState } from "react";
import {
    StatsModal,
    Modal,
    Pagination,
    Table,
    RinexAddModal,
} from "@componentsReact";
import { PlusCircleIcon } from "@heroicons/react/24/outline";

import { getStationInfoService } from "@services";

import { useAuth } from "@hooks/useAuth";
import useApi from "@hooks/useApi";

import { showModal, woTz } from "@utils";

import {
    GetParams,
    RinexRelatedStationInfo,
    StationData,
    StationInfoData,
    StationInfoServiceData,
} from "@types";

interface StationInfoModalProps {
    close: boolean;
    size?: "sm" | "md" | "lg" | "xl" | "fit";
    rinexStationInfo?: RinexRelatedStationInfo[] | undefined;
    station?: StationData | undefined;
    setModalState: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
    refetch: () => void;
}

const StationInfoModal = ({
    close,
    station,
    rinexStationInfo,
    size,
    setModalState,
    refetch,
}: StationInfoModalProps) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const [typeAddition, setTypeAddition] = useState<
        "last" | "none-clear" | undefined
    >(undefined);

    const [stationInfo, setStationInfo] = useState<StationInfoData | undefined>(
        undefined,
    );
    const [lastStationInfo, setLastStationInfo] = useState<
        StationInfoData | undefined
    >(undefined);
    const [stationInfos, setStationInfos] = useState<
        StationInfoData[] | undefined
    >(undefined);

    const [totalCount, setTotalCount] = useState<number>(0);

    const [loading, setLoading] = useState<boolean>(true);

    const {
        api_id, // eslint-disable-line
        network_code, // eslint-disable-line
        station_code, // eslint-disable-line
        receiver_vers, // eslint-disable-line
        ...restOfStationInfo
    } = stationInfos?.[0] || {};

    const AddStationInfoDropdown = () => (
        <div className="dropdown dropdown-end dropdown-hover">
            <summary role="button" className="btn btn-ghost m-1">
                <label className="self-center">Add</label>
                <PlusCircleIcon
                    strokeWidth={1.5}
                    stroke="currentColor"
                    className="w-8 h-10"
                />
            </summary>
            <ul
                tabIndex={0}
                className="dropdown-content menu bg-base-200 rounded-box z-[1] w-48 p-2 shadow"
            >
                <li className="font-semibold">
                    <a
                        onClick={() => {
                            setModals({
                                show: true,
                                title: "EditStats",
                                type: "none",
                            });
                            setStationInfo(lastStationInfo);
                            setTypeAddition("last");
                        }}
                    >
                        From last record
                    </a>
                </li>
                <li className="font-semibold">
                    <a
                        onClick={() => {
                            setModals({
                                show: true,
                                title: "EditStats",
                                type: "add",
                            });
                            setStationInfo(undefined);
                            setTypeAddition("none-clear");
                        }}
                    >
                        From empty record
                    </a>
                </li>
                <li className="font-semibold">
                    <a
                        onClick={() => {
                            setModals({
                                show: true,
                                title: "RinexAdd",
                                type: "none",
                            });
                            setStationInfo(lastStationInfo);
                            setTypeAddition("none-clear");
                        }}
                    >
                        From file
                    </a>
                </li>
            </ul>
        </div>
    );

    const titles = Object.keys(restOfStationInfo || {}).sort((a, b) => {
        if (a === "date_start") return -1;
        if (b === "date_start") return 1;
        if (a === "date_end") return -1;
        if (b === "date_end") return 1;
        return 0;
    });
    const tableData = stationInfos?.map(
        ({
            api_id, // eslint-disable-line
            network_code, // eslint-disable-line
            station_code, // eslint-disable-line
            receiver_vers, // eslint-disable-line
            date_start,
            date_end,
            ...restOfStationInfo
        }: StationInfoData) => {
            return [date_start, date_end, ...Object.values(restOfStationInfo)];
        },
    );

    const REGISTERS_PER_PAGE = 15; // Es el mismo que params.limit

    const bParams: GetParams = useMemo(() => {
        return {
            network_code: station?.network_code,
            station_code: station?.station_code,
            limit: REGISTERS_PER_PAGE,
            offset: 0,
        };
    }, [station]);

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [params, setParams] = useState<GetParams>(bParams);

    // PAGINATION... HEADACHE
    const [activePage, setActivePage] = useState<number>(1);
    const [pages, setPages] = useState<number>(0);
    const PAGES_TO_SHOW = 2;

    const getAllStationInfo = async (totalCount: number, limit = 0) => {
        let allData: StationInfoData[] = [];

        const newParams = {
            ...params,
            limit: limit,
            offset: totalCount,
        };

        const res = await getStationInfoService<StationInfoServiceData>(
            api,
            newParams,
        );

        allData = [...res.data];

        return allData;
    };

    const getStationInfo = async () => {
        try {
            setLoading(true);
            const res = await getStationInfoService<StationInfoServiceData>(
                api,
                bParams,
            );

            let allData = res.data;
            setTotalCount(res.total_count);
            if (rinexStationInfo) {
                allData = await getAllStationInfo(res.total_count);
                allData = allData.filter((st) =>
                    rinexStationInfo.some((r) => r.api_id === st.api_id),
                );
            }

            const totalItems = rinexStationInfo
                ? allData.length
                : res.total_count;
            const totalPages = Math.ceil(totalItems / REGISTERS_PER_PAGE);

            setStationInfos(allData);
            setPages(totalPages);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const getLastStationInfo = async () => {
        const arrInfo = await getAllStationInfo(totalCount - 1, totalCount);

        const last = arrInfo[arrInfo.length - 1] as StationInfoData;

        const lastStartDate = last.date_end
            ? new Date(last.date_end)
            : woTz(new Date());
        if (lastStartDate instanceof Date) {
            lastStartDate.setSeconds(lastStartDate.getSeconds() + 1);
            const formmatedLast = {
                ...last,
                date_start: lastStartDate?.toISOString(),
                date_end: "",
            };
            setLastStationInfo(formmatedLast);
        }
    };

    const paginateStationInfo = async (newParams: GetParams) => {
        try {
            setLoading(true);
            const res = await getStationInfoService<StationInfoServiceData>(
                api,
                newParams,
            );

            let filteredData = res.data;
            if (rinexStationInfo) {
                filteredData = res.data.filter((st) =>
                    rinexStationInfo.some((r) => r.api_id === st.api_id),
                );
            }

            setStationInfos(filteredData);

            const totalItems = rinexStationInfo
                ? filteredData.length
                : res.total_count;
            const totalPages = Math.ceil(totalItems / REGISTERS_PER_PAGE);
            setPages(totalPages);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handlePage = (page: number) => {
        if (page < 1 || page > pages) return;

        const newParams = {
            ...params,
            limit: REGISTERS_PER_PAGE,
            offset: REGISTERS_PER_PAGE * (page - 1),
        };

        setParams(newParams);
        setActivePage(page);
        paginateStationInfo(newParams);
    };

    const refetchDefault = async () => {
        // Calcula los parámetros de la página actual
        const paramsToUse = {
            ...params,
            limit: REGISTERS_PER_PAGE,
            offset: REGISTERS_PER_PAGE * (activePage - 1),
        };

        const res = await getStationInfoService<StationInfoServiceData>(
            api,
            paramsToUse,
        );

        let allData = res.data;
        if (rinexStationInfo) {
            allData = allData.filter((st) =>
                rinexStationInfo.some((r) => r.api_id === st.api_id),
            );
        }

        // Si la página actual quedó vacía, ir a la anterior
        if (allData.length === 0 && activePage > 1) {
            const prevParams = {
                ...params,
                limit: REGISTERS_PER_PAGE,
                offset: REGISTERS_PER_PAGE * (activePage - 2), // ESTO ES PORQUE ESTOY EN LA PAG 2, Y QUIERO VER LOS REGISTROS DE LA PAG 1, ENTONCES PARTO DESDE UN OFFSET EN 0
            };
            setActivePage(activePage - 1);
            const prevRes = await getStationInfoService<StationInfoServiceData>(
                api,
                prevParams,
            );
            let prevData = prevRes.data;
            if (rinexStationInfo) {
                prevData = prevData.filter((st) =>
                    rinexStationInfo.some((r) => r.api_id === st.api_id),
                );
            }
            setStationInfos(prevData);
        } else {
            setStationInfos(allData);
        }

        //Actualizar el total Pages
        const totalPages = Math.ceil(res.total_count / REGISTERS_PER_PAGE);
        setPages(totalPages);
        setTotalCount(res.total_count);
    };

    useEffect(() => {
        if (station && bParams.network_code && bParams.station_code) {
            getStationInfo();
        }
    }, [station]); // eslint-disable-line

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    useEffect(() => {
        if (totalCount > 0) {
            getLastStationInfo();
        }
    }, [totalCount, stationInfos]); // eslint-disable-line

    return (
        <Modal
            close={close}
            modalId={"Information"}
            size={size}
            setModalState={setModalState}
            handleCloseModal={refetch}
        >
            <div className="w-full inline-flex">
                <h3 className="font-bold text-center text-3xl my-2 grow">
                    {station?.station_code.toUpperCase()}
                </h3>

                <AddStationInfoDropdown />
            </div>
            <Table
                titles={titles}
                body={tableData}
                loading={loading}
                table={"Station"}
                dataOnly={false}
                onClickFunction={() => {
                    setModals({
                        show: true,
                        title: "EditStats",
                        type: "edit",
                    });
                }}
                setState={setStationInfo}
                state={stationInfos}
                dataFetchUrl={"api/station-info"}
            />
            {stationInfos && stationInfos?.length > 0 && (
                <Pagination
                    pages={pages}
                    pagesToShow={PAGES_TO_SHOW}
                    activePage={activePage}
                    handlePage={handlePage}
                />
            )}
            {modals?.show && modals?.title === "EditStats" && (
                <StatsModal
                    stationInfo={stationInfo}
                    typeAddition={typeAddition}
                    modalType={modals.type}
                    reFetch={refetchDefault}
                    setStateModal={setModals}
                    setStationInfo={setStationInfo}
                    setTypeAddition={setTypeAddition}
                />
            )}
            {modals?.show && modals?.title === "RinexAdd" && (
                <RinexAddModal
                    stationApiId={station?.api_id ?? 0}
                    setModalState={setModals}
                    handleCloseModal={() => {
                        setActivePage(1);
                        getStationInfo();
                    }}
                />
            )}
        </Modal>
    );
};

export default StationInfoModal;

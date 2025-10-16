import { useEffect, useMemo, useState } from "react";

import { Pagination, Table } from "@componentsReact";

import { XMarkIcon } from "@heroicons/react/24/outline";

import { GetParams, StationData } from "@types";

interface Props {
    stations: StationData[] | undefined;
    mainParams: GetParams;
    setState: React.Dispatch<React.SetStateAction<boolean>>;
}

const StationsModal = ({ stations, mainParams, setState }: Props) => {
    const [paginatedStations, setPaginatedStations] = useState<
        StationData[] | undefined
    >(undefined);

    const bParams: GetParams = useMemo(() => {
        return {
            limit: 5,
            offset: 0,
        };
    }, []);

    const [params, setParams] = useState<GetParams>(bParams);

    // PAGINATION... HEADACHE
    const [activePage, setActivePage] = useState<number>(1);
    const [pages, setPages] = useState<number>(0);
    const PAGES_TO_SHOW = 2;
    const REGISTERS_PER_PAGE = 5; // Es el mismo que params.limit

    useEffect(() => {
        if (stations) {
            setPages(Math.ceil(stations?.length / REGISTERS_PER_PAGE));
            setPaginatedStations(
                stations
                    .sort((a, b) =>
                        a.station_code.localeCompare(b.station_code),
                    )
                    .slice(0, REGISTERS_PER_PAGE),
            );
        }
    }, [stations]);

    const handlePage = (page: number) => {
        if (page < 1 || page > pages) return;
        let newParams;
        if (page === 1) {
            newParams = {
                ...params,
                limit: REGISTERS_PER_PAGE * 1,
                offset: REGISTERS_PER_PAGE * (page - 1),
            };
        } else {
            newParams = {
                ...params,
                limit: REGISTERS_PER_PAGE,
                offset: REGISTERS_PER_PAGE * (page - 1),
            };
        }

        setParams(newParams);
        setActivePage(page);
        setPaginatedStations(
            stations
                ?.sort((a, b) => a.station_code.localeCompare(b.station_code))
                .slice(
                    REGISTERS_PER_PAGE * (page - 1),
                    REGISTERS_PER_PAGE * page,
                ),
        );
    };

    const bTitles = {
        station: String,
        country_code: String,
        station_name: String,
        lat: Number,
        lon: Number,
        dome: String,
    };

    const titles = Object.keys(bTitles || {});

    /* eslint-disable */
    const tableData = paginatedStations
        ?.sort((a, b) => a.station_code.localeCompare(b.station_code))
        .map(
            ({
                harpos_coeff_otl,
                date_start,
                date_end,
                marker,
                api_id,
                auto_x,
                auto_y,
                auto_z,
                height,
                max_dist,
                // station_code,
                // network_code,
                ...st
            }: StationData) => {
                const orderedStation = {
                    station:
                        st.station_code.toUpperCase() +
                        "." +
                        st.network_code.toUpperCase(),
                    country_code: st.country_code,
                    station_name: st.station_name,
                    lat: st.lat?.toFixed(8) ?? "-",
                    lon: st.lon?.toFixed(8) ?? "-",
                    dome: st.dome,
                };
                return Object.values(orderedStation);
            },
        );
    /* eslint-enable */

    return (
        <div className="flex flex-col">
            <div className="card bg-base-200 p-4 space-y-2 w-[700px]">
                <div className="w-full inline-flex">
                    <h3 className="font-bold text-center text-3xl my-2 grow">
                        Stations
                    </h3>
                    <XMarkIcon
                        className="btn btn-circle"
                        style={{
                            width: "26px",
                            height: "26px",
                            minHeight: 0,
                        }}
                        onClick={() => setState(false)}
                    />
                </div>
                <Table
                    titles={titles}
                    body={tableData}
                    table={"Stations"}
                    alterInfo={mainParams}
                    dataOnly={true}
                    onClickFunction={() => undefined}
                    state={paginatedStations}
                    dataFetchUrl="api/stations"
                />
                {stations && stations?.length > 0 && (
                    <Pagination
                        pages={pages}
                        pagesToShow={PAGES_TO_SHOW}
                        activePage={activePage}
                        handlePage={handlePage}
                    />
                )}
            </div>
        </div>
    );
};

export default StationsModal;

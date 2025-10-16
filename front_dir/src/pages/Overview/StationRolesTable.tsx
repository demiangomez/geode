import { useEffect, useMemo, useState } from "react";
import {
    StationRoleModal,
    Pagination,
    Table,
    TableCard,
} from "@componentsReact";

import { useAuth, useApi } from "@hooks";

import { getStationRolesService } from "@services";
import { showModal } from "@utils";

import { GetParams, StationStatus, StationStatusServiceData } from "@types";

const StationRolesTable = () => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const bParams: GetParams = useMemo(() => {
        return {
            limit: 5,
            offset: 0,
        };
    }, []);

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [loading, setLoading] = useState<boolean>(true);
    const [params, setParams] = useState<GetParams>(bParams);

    const [stationRoles, setStationRoles] = useState<StationStatus[]>([]);
    const [stationRole, setStationRole] = useState<StationStatus | undefined>(
        undefined,
    );

    const [activePage, setActivePage] = useState<number>(1);
    const [pages, setPages] = useState<number>(0);
    const PAGES_TO_SHOW = 2;
    const REGISTERS_PER_PAGE = 5; // Es el mismo que params.limit

    const getStationRoles = async () => {
        try {
            setLoading(true);
            const res = await getStationRolesService<StationStatusServiceData>(
                api,
                params,
            );
            setStationRoles(res.data);
            if (bParams.limit) {
                setPages(Math.ceil(res.total_count / bParams.limit));
            }
            res.data && res.data.length === 0 && handlePage(1);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const paginateMonuments = async (newParams: GetParams) => {
        try {
            setLoading(true);
            const res = await getStationRolesService<StationStatusServiceData>(
                api,
                newParams,
            );
            setStationRoles(res.data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

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
        paginateMonuments(newParams);
    };

    const reFetch = () => {
        getStationRoles();
    };

    useEffect(() => {
        getStationRoles();
    }, []); // eslint-disable-line

    const titles = ["Name"];

    const body = useMemo(() => {
        return stationRoles
            ?.sort((a, b) => a.name.localeCompare(b.name))
            .map((sr) =>
                Object.values({
                    // id: monument.id,
                    name: sr.name,
                }),
            );
    }, [stationRoles]);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);
    return (
        <TableCard
            title={"Station Roles"}
            size={"650px"}
            addButtonTitle="+ Role"
            modalTitle="EditStationRole"
            setModals={setModals}
            addButton={true}
        >
            <Table
                titles={body && body.length > 0 ? titles : []}
                body={body}
                table={"StationRole"}
                loading={loading}
                dataOnly={false}
                onClickFunction={() =>
                    setModals({
                        show: true,
                        title: "EditStationRole",
                        type: "edit",
                    })
                }
                setState={setStationRole}
                state={stationRoles}
                dataFetchUrl={"api/station-roles"}
            />
            {body && body.length > 0 ? (
                <Pagination
                    pages={pages}
                    pagesToShow={PAGES_TO_SHOW}
                    activePage={activePage}
                    handlePage={handlePage}
                />
            ) : null}
            {modals?.show && modals.title === "EditStationRole" && (
                <StationRoleModal
                    Role={stationRole}
                    modalType={modals.type}
                    setStateModal={setModals}
                    setRole={setStationRole}
                    reFetch={reFetch}
                />
            )}
        </TableCard>
    );
};

export default StationRolesTable;

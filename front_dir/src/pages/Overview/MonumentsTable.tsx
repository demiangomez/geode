import { useEffect, useMemo, useState } from "react";
import { MonumentModal, Pagination, Table, TableCard } from "@componentsReact";

import { useAuth, useApi } from "@hooks";

import { getMonumentsTypesService } from "@services";
import { showModal } from "@utils";

import { GetParams, MonumentTypes, MonumentTypesServiceData } from "@types";

const MonumentsTable = () => {
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

    const [monuments, setMonuments] = useState<MonumentTypes[]>([]);
    const [monument, setMonument] = useState<MonumentTypes | undefined>(
        undefined,
    );

    const [activePage, setActivePage] = useState<number>(1);
    const [pages, setPages] = useState<number>(0);
    const PAGES_TO_SHOW = 2;
    const REGISTERS_PER_PAGE = 5; // Es el mismo que params.limit

    const getMonuments = async () => {
        try {
            setLoading(true);
            const res =
                await getMonumentsTypesService<MonumentTypesServiceData>(
                    api,
                    params,
                );
            setMonuments(res.data);
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
            const res =
                await getMonumentsTypesService<MonumentTypesServiceData>(
                    api,
                    newParams,
                );
            setMonuments(res.data);
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
        getMonuments();
    };

    useEffect(() => {
        getMonuments();
    }, []); // eslint-disable-line

    const titles = ["Name", "Photo"];

    const body = useMemo(() => {
        return monuments
            ?.sort((a, b) => a.name.localeCompare(b.name))
            .map((monument) =>
                Object.values({
                    // id: monument.id,
                    name: monument.name,
                    photo: monument.photo_file,
                }),
            );
    }, [monuments]);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    return (
        <TableCard
            title={"Monuments"}
            size={"750px"}
            addButtonTitle="+ Monument"
            modalTitle="EditMonuments"
            setModals={setModals}
            addButton={true}
        >
            <Table
                titles={body && body.length > 0 ? titles : []}
                body={body}
                table={"Monuments"}
                loading={loading}
                dataOnly={false}
                onClickFunction={() =>
                    setModals({
                        show: true,
                        title: "EditMonuments",
                        type: "edit",
                    })
                }
                setState={setMonument}
                state={monuments}
                dataFetchUrl={"api/monument-types"}
            />
            {body && body.length > 0 ? (
                <Pagination
                    pages={pages}
                    pagesToShow={PAGES_TO_SHOW}
                    activePage={activePage}
                    handlePage={handlePage}
                />
            ) : null}
            {modals?.show && modals.title === "EditMonuments" && (
                <MonumentModal
                    Monument={monument}
                    modalType={modals.type}
                    setStateModal={setModals}
                    setMonument={setMonument}
                    reFetch={reFetch}
                />
            )}
        </TableCard>
    );
};

export default MonumentsTable;

import { useEffect, useMemo, useState } from "react";
import {
    AddCampaignModal,
    Pagination,
    StationSelectModal,
    Table,
    TableCard,
    VisitsCampaignModal,
} from "@componentsReact";

import { useAuth, useApi } from "@hooks";

import { showModal } from "@utils";
import { getPeopleService, getStationCampaignsService } from "@services";

import {
    CampaignsData,
    CampaignsServiceData,
    GetParams,
    People,
    PeopleServiceData,
} from "@types";

const CampaignsTable = () => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [loading, setLoading] = useState<boolean>(false);

    const [people, setPeople] = useState<People[] | undefined>(undefined);

    const [campaigns, setCampaigns] = useState<CampaignsData[] | undefined>(
        undefined,
    );
    const [campaign, setCampaign] = useState<CampaignsData | undefined>(
        undefined,
    );

    const [activePage, setActivePage] = useState<number>(1);
    const [pages, setPages] = useState<number>(0);
    const PAGES_TO_SHOW = 2;
    const REGISTERS_PER_PAGE = 5; // Es el mismo que params.limit

    const bParams: GetParams = useMemo(() => {
        return {
            limit: REGISTERS_PER_PAGE,
            offset: 0,
        };
    }, []);

    const [params, setParams] = useState<GetParams>(bParams);

    const getPeople = async () => {
        try {
            setLoading(true);
            const res = await getPeopleService<PeopleServiceData>(api, {
                without_photo: true,
            });
            setPeople(res.data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const getCampaigns = async (paramsToUse = bParams) => {
        try {
            setLoading(true);
            const res = await getStationCampaignsService<CampaignsServiceData>(
                api,
                paramsToUse,
            );
            setCampaigns(res.data);

            if (bParams.limit) {
                setPages(Math.ceil(res.total_count / bParams.limit));
            }
            return res.data;
        } catch (err) {
            console.error(err);
            return [];
        } finally {
            setLoading(false);
        }
    };

    const paginateCampaigns = async (newParams: GetParams) => {
        try {
            setLoading(true);
            const res = await getStationCampaignsService<CampaignsServiceData>(
                api,
                newParams,
            );
            setCampaigns(res.data);
            if (bParams.limit) {
                setPages(Math.ceil(res.total_count / bParams.limit));
            }
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
        paginateCampaigns(newParams);
    };

    const reFetch = async () => {
        const actualPage = activePage;
        const paramsToUse = {
            ...params,
            limit: REGISTERS_PER_PAGE,
            offset: REGISTERS_PER_PAGE * (actualPage - 1),
        };
        const data = await getCampaigns(paramsToUse);

        if (data.length === 0 && actualPage > 1) {
            // Si la página actual quedó vacía, ir a la anterior
            const prevParams = {
                ...params,
                limit: REGISTERS_PER_PAGE,
                offset: REGISTERS_PER_PAGE * (actualPage - 2),
            };
            setActivePage(actualPage - 1);
            await getCampaigns(prevParams);
        }
    };

    useEffect(() => {
        getCampaigns();
        getPeople();
    }, []); // eslint-disable-line

    const titles = ["Name", "Start Date", "End Date", "Default People"];

    const body = useMemo(() => {
        return campaigns?.map((campaign) => {
            const defaultPeople = campaign.default_people
                .map((person) => {
                    const personData = people?.find((p) => p.id === person);
                    return personData?.first_name + " " + personData?.last_name;
                })
                .join(", ");
            return Object.values({
                name: campaign.name,
                start_date: campaign.start_date,
                end_date: campaign.end_date,
                default_people: defaultPeople,
            });
        });
    }, [campaigns, people]);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    return (
        <TableCard
            title={"Campaigns"}
            size={"80vw"}
            addButtonTitle="+ Campaign"
            modalTitle="EditCampaigns"
            setModals={setModals}
            addButton={true}
        >
            <Table
                titles={body && body.length > 0 ? titles : []}
                body={body}
                table={"Campaigns"}
                loading={loading}
                dataOnly={false}
                buttonRegister={true}
                visitsRegister={true}
                onClickFunction={() =>
                    setModals({
                        show: true,
                        title: "EditCampaigns",
                        type: "edit",
                    })
                }
                onAlterClickFunction={() =>
                    setModals({
                        show: true,
                        title: "SelectStation",
                        type: "edit",
                    })
                }
                setState={setCampaign}
                state={campaigns}
                onVisitsClickFunction={() =>
                    setModals({
                        show: true,
                        title: "Visits",
                        type: "edit",
                    })
                }
                dataFetchUrl="api/campaigns"
            />
            {body && body.length > 0 ? (
                <Pagination
                    pages={pages}
                    pagesToShow={PAGES_TO_SHOW}
                    activePage={activePage}
                    handlePage={handlePage}
                />
            ) : null}
            {modals?.show && modals.title === "EditCampaigns" && (
                <AddCampaignModal
                    campaign={campaign}
                    modalType={modals.type}
                    setStateModal={setModals}
                    reFetch={() => {
                        reFetch();
                    }}
                    people={people}
                />
            )}

            {modals?.show && modals.title === "SelectStation" && (
                <StationSelectModal
                    campaign={campaign}
                    setCampaign={setCampaign}
                    setStateModal={setModals}
                />
            )}

            {modals?.show && modals.title === "Visits" && (
                <VisitsCampaignModal
                    campaign={campaign}
                    campaingId={campaign?.id}
                    setCampaign={setCampaign}
                    setModals={setModals}
                />
            )}
        </TableCard>
    );
};

export default CampaignsTable;

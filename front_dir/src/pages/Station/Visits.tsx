import { useLocation, useOutletContext } from "react-router-dom";
import { useEffect, useState } from "react";
import {
    CardContainer,
    ConfirmDeleteModal,
    TableCard,
    TableSkeleton,
    VisitAddModal,
    VisitDetailModal,
    VisitThumbNail,
} from "@componentsReact";

import { useAuth, useApi } from "@hooks";

import {
    delStationVisitService,
    getStationCampaignsService,
    getStationVisitsImagesService,
    getStationVisitsService,
    getStationStatusService,
    getStationTypesService,
} from "@services";

import {
    ErrorResponse,
    Errors,
    StationCampaignsData,
    StationCampaignsServiceData,
    StationData,
    StationVisitsData,
    StationVisitsFilesData,
    StationVisitsFilesServiceData,
    StationVisitsServiceData,
    StationStatusServiceData,
    StationStatusData,
    StationTypeData,
    StationTypeServiceData,
} from "@types";

import { showModal } from "@utils";

interface OutletContext {
    station: StationData;
}

const Visits = () => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const location = useLocation();

    const campaign = location.state
        ? "visitDetail" in location.state
            ? undefined
            : "start_date" in location.state
              ? (location.state as StationCampaignsData)
              : undefined
        : undefined;

    const visitByCampaign = location.state
        ? "visitDetail" in location.state
            ? (location.state.visitDetail as StationVisitsData)
            : undefined
        : undefined;

    const { station } = useOutletContext<OutletContext>();

    const [loading, setLoading] = useState<boolean>(false);
    const [loadingVisitImages, setLoadingVisitImages] =
        useState<boolean>(false);

    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [visits, setVisits] = useState<StationVisitsData[] | undefined>(
        undefined,
    );

    const [types, setTypes] = useState<{ image: string; name: string }[]>([]);
    const [statuses, setStatuses] = useState<{ name: string; color: string }[]>(
        [],
    );

    const [visitToDel, setVisitToDel] = useState<number | undefined>(undefined);

    const [campaigns, setCampaigns] = useState<
        StationCampaignsData[] | undefined
    >(undefined);

    const [images, setImages] = useState<StationVisitsFilesData[] | undefined>(
        undefined,
    );

    const [visit, setVisit] = useState<StationVisitsData | undefined>(
        undefined,
    );

    const getVisits = async () => {
        try {
            setLoading(true);
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
        } finally {
            setLoading(false);
        }
    };

    const getCampaigns = async () => {
        try {
            setLoading(true);
            const res =
                await getStationCampaignsService<StationCampaignsServiceData>(
                    api,
                    {
                        limit: 0,
                        offset: 0,
                    },
                );

            if (res.statusCode === 200) {
                setCampaigns(res.data);
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    const getVisitsImages = async () => {
        try {
            setLoadingVisitImages(true);
            const res =
                await getStationVisitsImagesService<StationVisitsFilesServiceData>(
                    api,
                    {
                        limit: 0,
                        offset: 0,
                        station_api_id: String(station?.api_id),
                        thumbnail: true,
                    },
                );

            if (res.statusCode === 200) {
                setImages(res.data);
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoadingVisitImages(false);
        }
    };

    const getStationStatuses = async () => {
        try {
            const res =
                await getStationStatusService<StationStatusServiceData>(api);
            if (res) {
                const statuses = res.data.map((status: StationStatusData) => {
                    return {
                        color: status.color_name,
                        name: status.name,
                    };
                });
                setStatuses(statuses);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const getStationTypes = async () => {
        try {
            const res =
                await getStationTypesService<StationTypeServiceData>(api);
            if (res) {
                const types = res.data.map((type: StationTypeData) => {
                    return {
                        image: type.actual_image as string,
                        name: type.name,
                    };
                });
                setTypes(types);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const delVisit = async () => {
        try {
            setLoading(true);

            const res = await delStationVisitService<ErrorResponse>(
                api,
                visitToDel ?? 0,
            );

            if ("status" in res && res.status === "success") {
                setMsg({
                    status: res.statusCode,
                    msg: res.msg,
                });
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (station && station.api_id) {
            getVisits();
            getVisitsImages();
            getCampaigns();
            getStationStatuses();
            getStationTypes();
        }
    }, [station]); // eslint-disable-line

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    useEffect(() => {
        if (campaign && !visitByCampaign) {
            setModals({
                show: true,
                title: "AddVisit",
                type: "edit",
            });
        } else if (!campaign && visitByCampaign) {
            setModals({
                show: true,
                title: "VisitDetail",
                type: "none",
            });
            setVisit(visitByCampaign);
        }
    }, [campaign, visitByCampaign]);

    return (
        <div className="">
            <h1 className="text-2xl font-base text-center">VISITS</h1>

            <div className="flex flex-grow w-full justify-center pr-2 space-x-2 px-2 pb-4">
                <CardContainer title="" titlePosition="start">
                    <TableCard
                        title={"Visits"}
                        size={"100%"}
                        addButtonTitle="Add Visit"
                        modalTitle="AddVisit"
                        setModals={setModals}
                        addButton={true}
                    >
                        {loading ? (
                            <div className="grid gap-4 grid-cols-3 grid-flow-dense">
                                {Array.from({ length: 3 }).map((_, i) => (
                                    <TableSkeleton mainSize="300px" key={i} />
                                ))}
                            </div>
                        ) : visits && visits.length === 0 ? (
                            <div className="text-center text-neutral text-2xl font-bold w-full rounded-md bg-neutral-content p-6">
                                There are no visits
                            </div>
                        ) : (
                            <div
                                className={`grid 
                                    grid-cols-2
                                    grid-flow-dense gap-4`}
                            >
                                {visits?.map((vis) => {
                                    const visitImages = images?.filter(
                                        (i) => i.visit === vis.id,
                                    );

                                    return (
                                        <VisitThumbNail
                                            key={vis.id}
                                            station={station}
                                            visit={vis}
                                            setModals={setModals}
                                            setVisitToDel={setVisitToDel}
                                            setVisit={setVisit}
                                            campaigns={campaigns}
                                            loadingVisitImages={
                                                loadingVisitImages
                                            }
                                            visitImages={visitImages}
                                            statuses={statuses}
                                            types={types}
                                        />
                                    );
                                })}
                            </div>
                        )}
                    </TableCard>
                </CardContainer>
            </div>

            {modals && modals?.title === "ConfirmDelete" && (
                <ConfirmDeleteModal
                    loading={loading}
                    msg={msg}
                    confirmRemove={() => delVisit()}
                    closeModal={() => {
                        setModals({
                            show: false,
                            title: "",
                            type: "edit",
                        });
                        setMsg(undefined);
                        getVisits();
                        getVisitsImages();
                        getCampaigns();
                    }}
                />
            )}

            {modals?.show && modals?.title === "AddVisit" && (
                <VisitAddModal
                    campaigns={campaigns}
                    campaignB={campaign}
                    setStateModal={setModals}
                    station={station}
                    closeModal={() => {
                        getVisits();
                        getVisitsImages();
                        getCampaigns();
                        setModals({
                            show: false,
                            title: "",
                            type: "edit",
                        });
                    }}
                    reFetch={() => {
                        getVisits();
                        getVisitsImages();
                        getCampaigns();
                    }}
                />
            )}
            {modals?.show && modals.title === "VisitDetail" && (
                <VisitDetailModal
                    campaigns={campaigns}
                    visitId={visit?.id}
                    setStateModal={setModals}
                    closeModal={() => {
                        getVisits();
                        getVisitsImages();
                        getCampaigns();
                        setVisit(undefined);
                    }}
                />
            )}
        </div>
    );
};

export default Visits;

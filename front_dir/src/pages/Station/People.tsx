import { useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";

import {
    StationPersonModal,
    CardContainer,
    Table,
    TableCard,
    ConfirmDeleteModal,
    StationPeopleModal,
} from "@componentsReact";

import {
    delRolePersonStationService,
    getPeopleService,
    getRolePersonStationService,
    getStationRolesService,
} from "@services";

import { useAuth, useApi } from "@hooks";

import { showModal } from "@utils";

import {
    People as PeopleType,
    PeopleServiceData,
    RolePersonStationData,
    RolePersonStationServiceData,
    StationData,
    Errors,
    ErrorResponse,
    StationStatusServiceData,
    StationStatus,
} from "@types";

interface OutletContext {
    station: StationData;
}

const People = () => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const { station } = useOutletContext<OutletContext>();
    const stationID = (station?.api_id ?? "").toString();

    const [loading, setLoading] = useState<boolean>(false);
    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [rolePersonStations, setRolePersonStations] = useState<
        RolePersonStationData[] | undefined
    >(undefined);

    const [rolePersonStation, setRolePersonStation] = useState<
        RolePersonStationData | undefined
    >(undefined);

    const [peopleType, setPeopleType] = useState<PeopleType[]>([]);
    const [person, setPerson] = useState<PeopleType | undefined>(undefined);

    const [roles, setRoles] = useState<StationStatus[] | undefined>(undefined);

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const getAll = async () => {
        try {
            setLoading(true);

            const res =
                await getRolePersonStationService<RolePersonStationServiceData>(
                    api,
                    {
                        station_api_id: stationID,
                        offset: 0,
                        limit: 0,
                    },
                );
            setRolePersonStations(res.data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const reFetch = () => {
        getPeople();
    };

    const delPerson = async () => {
        try {
            setLoading(true);

            const res = await delRolePersonStationService<ErrorResponse>(
                api,
                Number(rolePersonStation?.id),
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

    const getPeople = async () => {
        try {
            setLoading(true);
            const res = await getPeopleService<PeopleServiceData>(api);
            setPeopleType(res.data);
            // setPages(Math.ceil(res.total_count / bParams.limit));
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const getRoles = async () => {
        try {
            const res =
                await getStationRolesService<StationStatusServiceData>(api);
            setRoles(res.data);
        } catch (err) {
            console.error(err);
        }
    };

    useEffect(() => {
        getAll();
        getPeople();
        getRoles();
    }, []); // eslint-disable-line

    const titles = [
        "Role",
        "Name",
        "Last Name",
        "Email",
        "Address",
        "Phone",
        "Institution",
        "Position",
    ];

    const body = useMemo(() => {
        const b = rolePersonStations?.map((rp) =>
            Object.values({
                // id: monument.id,
                role: roles?.find((r) => r.id === rp.role)?.name,
                name: peopleType?.find((p) => p.id === rp.person)?.first_name,
                last_name: peopleType.find((p) => p.id === rp.person)
                    ?.last_name,
                email: peopleType.find((p) => p.id === rp.person)?.email,
                address: peopleType.find((p) => p.id === rp.person)?.address,
                phone: peopleType.find((p) => p.id === rp.person)?.phone,
                institution: peopleType.find((p) => p.id === rp.person)
                    ?.institution,
                position: peopleType.find((p) => p.id === rp.person)?.position,
            }),
        );

        if (!b) return [];
        return b?.sort((a, b) => {
            const aValue = a[2] || "";
            const bValue = b[2] || "";

            if (aValue < bValue) {
                return -1;
            }
            if (aValue > bValue) {
                return 1;
            }
            return 0;
        });
    }, [rolePersonStations, peopleType, roles]);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    return (
        <div className="">
            <h1 className="text-2xl font-base text-center">PEOPLE</h1>
            <div className="flex w-full justify-center pr-2 space-x-2 px-2">
                <CardContainer title="" titlePosition="start">
                    <TableCard
                        title={"People"}
                        size={"100%"}
                        addButtonTitle="Link Person"
                        modalTitle="EditStationPerson"
                        setModals={setModals}
                        addButton={true}
                        secondAddButton={true}
                        secondAddButtonTitle="+ Person"
                        secondModalTitle="EditPerson"
                    >
                        <Table
                            titles={body && body.length > 0 ? titles : []}
                            body={body}
                            table={"People"}
                            loading={loading}
                            dataOnly={true}
                            deleteRegister={true}
                            onClickFunction={() =>
                                setModals({
                                    show: true,
                                    title: "ConfirmDelete",
                                    type: "edit",
                                })
                            }
                            setState={setRolePersonStation}
                            state={rolePersonStations}
                        />
                        {modals?.show &&
                        modals.title === "EditStationPerson" ? (
                            <StationPersonModal
                                people={peopleType}
                                roles={roles}
                                Person={rolePersonStation}
                                Station={station}
                                modalType={modals.type}
                                setStateModal={setModals}
                                reFetch={() => getAll()}
                            />
                        ) : modals?.title === "EditPerson" ? (
                            <StationPeopleModal
                                Person={person}
                                modalType={modals.type}
                                setStateModal={setModals}
                                setPerson={setPerson}
                                reFetch={reFetch}
                                people={peopleType}
                            />
                        ) : null}

                        {modals && modals?.title === "ConfirmDelete" && (
                            <ConfirmDeleteModal
                                loading={loading}
                                msg={msg}
                                confirmRemove={() => delPerson()}
                                closeModal={() => {
                                    setModals({
                                        show: false,
                                        title: "",
                                        type: "edit",
                                    });
                                    setMsg(undefined);
                                    getAll();
                                }}
                            />
                        )}
                    </TableCard>
                </CardContainer>
            </div>
        </div>
    );
};

export default People;

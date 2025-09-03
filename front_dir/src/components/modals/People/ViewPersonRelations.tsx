import useApi from "@hooks/useApi";
import { useAuth } from "@hooks/useAuth";
import { getPersonRelations } from "@services";
import { People } from "@types";
import { useEffect, useState } from "react";
import Modal from "../Modal";
import Pagination from "@components/Pagination";
interface Props {
    Person: People | undefined;
    reFetch: () => void;
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
}

interface Relation {
    relations: {
        role_person_station: RolePersonStation[];
        visits: Visit[];
    };
    statusCode: number;
}

interface RolePersonStation {
    id: number;
    station_network_code: string;
    station_station_code: string;
    station_role_name: string;
    role: number;
    person: number;
    station: number;
}

interface Visit {
    id: number;
    station_network_code: string;
    station_station_code: string;
    date: string;
    people: number[];
    // Other fields not displayed but included in the API response
    log_sheet_actual_file: any;
    navigation_actual_file: any;
    campaign_name: any;
    observation_file_count: number;
    visit_image_count: number;
    other_file_count: number;
    log_sheet_filename: string;
    navigation_filename: string;
    comments: string;
    campaign: any;
    station: number;
}

const ViewPersonRelations = ({ Person, reFetch, setStateModal }: Props) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);
    const [relation, setRelation] = useState<Relation | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [rolesCurrentPage, setRolesCurrentPage] = useState<number>(1);
    const [visitsCurrentPage, setVisitsCurrentPage] = useState<number>(1);
    const itemsPerPage = 4;

    const getRelations = async () => {
        if (!Person?.id) return;

        try {
            setLoading(true);
            const res = await getPersonRelations<Relation>(api, Person.id);
            setRelation(res);
        } catch (error) {
            console.error("Error fetching person relations:", error);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        getRelations();
    }, [Person]);

    const handleCloseModal = () => {
        reFetch();
    };

    // Pagination helper functions
    const getPaginatedData = (data: any[], currentPage: number) => {
        const startIndex = (currentPage - 1) * itemsPerPage;
        const endIndex = startIndex + itemsPerPage;
        return data.slice(startIndex, endIndex);
    };

    return (
        <Modal
            close={true}
            modalId={"ViewPersonRelations"}
            size={"smPlus"}
            handleCloseModal={() => handleCloseModal()}
            setModalState={setStateModal}
        >
            <div className="p-4">
                <h2 className="text-xl font-bold mb-4">
                    Relations for {Person?.first_name} {Person?.last_name}
                </h2>

                {loading ? (
                    <div className="flex justify-center my-4">
                        <span className="loading loading-spinner loading-md"></span>
                    </div>
                ) : relation && relation.relations ? (
                    <div className="space-y-6">
                        {/* Station Roles Section */}
                        <div>
                            <h3 className="font-semibold text-lg mb-2">
                                Station Roles
                                {relation.relations.role_person_station &&
                                    relation.relations.role_person_station
                                        .length > 0 && (
                                        <span className="text-sm font-normal ml-2 text-gray-500">
                                            (
                                            {
                                                relation.relations
                                                    .role_person_station.length
                                            }{" "}
                                            total)
                                        </span>
                                    )}
                            </h3>
                            {relation.relations.role_person_station &&
                            relation.relations.role_person_station.length >
                                0 ? (
                                <div>
                                    <div className="overflow-x-auto">
                                        <table className="table table-zebra table-bordered w-full border border-base-300">
                                            <thead>
                                                <tr className="bg-base-200 border-b border-base-300">
                                                    <th className="border-r border-base-300">
                                                        Network Code
                                                    </th>
                                                    <th className="border-r border-base-300">
                                                        Station Code
                                                    </th>
                                                    <th>Role</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {getPaginatedData(
                                                    relation.relations
                                                        .role_person_station,
                                                    rolesCurrentPage,
                                                ).map((role) => (
                                                    <tr
                                                        key={role.id}
                                                        className="hover:bg-base-100 transition-colors duration-150 border-b border-base-300"
                                                    >
                                                        <td className="font-medium border-r border-base-300">
                                                            {
                                                                role.station_network_code
                                                            }
                                                        </td>
                                                        <td className="font-medium border-r border-base-300">
                                                            {
                                                                role.station_station_code
                                                            }
                                                        </td>
                                                        <td>
                                                            <span>
                                                                {
                                                                    role.station_role_name
                                                                }
                                                            </span>
                                                        </td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                    {relation.relations &&
                                        relation.relations?.role_person_station
                                            .length > 4 && (
                                            <Pagination
                                                pages={Math.ceil(
                                                    relation.relations
                                                        .role_person_station
                                                        .length / itemsPerPage,
                                                )}
                                                pagesToShow={2}
                                                activePage={rolesCurrentPage}
                                                handlePage={setRolesCurrentPage}
                                            />
                                        )}
                                </div>
                            ) : (
                                <div className="text-center py-4 bg-base-200 rounded-lg">
                                    <p className="text-gray-500">
                                        No station roles found
                                    </p>
                                </div>
                            )}
                        </div>

                        {/* Visits Section */}
                        <div>
                            <h3 className="font-semibold text-lg mb-2">
                                Station Visits
                                {relation.relations.visits &&
                                    relation.relations.visits.length > 0 && (
                                        <span className="text-sm font-normal ml-2 text-gray-500">
                                            ({relation.relations.visits.length}{" "}
                                            total)
                                        </span>
                                    )}
                            </h3>
                            {relation.relations.visits &&
                            relation.relations.visits.length > 0 ? (
                                <div>
                                    <div className="overflow-x-auto">
                                        <table className="table table-zebra table-bordered w-full border border-base-300">
                                            <thead>
                                                <tr className="bg-base-200 border-b border-base-300">
                                                    <th className="border-r border-base-300">
                                                        Network Code
                                                    </th>
                                                    <th className="border-r border-base-300">
                                                        Station Code
                                                    </th>
                                                    <th>Date</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {getPaginatedData(
                                                    relation.relations.visits,
                                                    visitsCurrentPage,
                                                ).map((visit) => (
                                                    <tr
                                                        key={visit.id}
                                                        className="hover:bg-base-100 transition-colors duration-150 border-b border-base-300"
                                                    >
                                                        <td className="font-medium border-r border-base-300">
                                                            {
                                                                visit.station_network_code
                                                            }
                                                        </td>
                                                        <td className="font-medium border-r border-base-300">
                                                            {
                                                                visit.station_station_code
                                                            }
                                                        </td>
                                                        <td>
                                                            <span className="text-sm">
                                                                {visit.date}
                                                            </span>
                                                        </td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                    {relation.relations &&
                                        relation.relations?.visits.length >
                                            4 && (
                                            <Pagination
                                                pages={Math.ceil(
                                                    relation.relations.visits
                                                        .length / itemsPerPage,
                                                )}
                                                pagesToShow={2}
                                                activePage={visitsCurrentPage}
                                                handlePage={
                                                    setVisitsCurrentPage
                                                }
                                            />
                                        )}
                                </div>
                            ) : (
                                <div className="text-center py-4 bg-base-200 rounded-lg">
                                    <p className="text-gray-500">
                                        No visits found
                                    </p>
                                </div>
                            )}
                        </div>
                    </div>
                ) : (
                    <div className="text-center py-4">
                        <p className="text-gray-500">
                            No relations found for this person.
                        </p>
                    </div>
                )}
            </div>
        </Modal>
    );
};

export default ViewPersonRelations;

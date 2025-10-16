import { useEffect, useMemo, useState } from "react";

import {
    StationPeopleModal,
    Pagination,
    Table,
    TableCard,
} from "@componentsReact";

import { useAuth, useApi } from "@hooks";

import { showModal } from "@utils";

import { getPeopleService } from "@services";

import { GetParams, People, PeopleServiceData } from "@types";
import MergePeopleModal from "@components/modals/MergePeopleModal";
import ViewPersonRelations from "@components/modals/People/ViewPersonRelations";

const PeopleTable = () => {
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

    const [allPeople, setAllPeople] = useState<People[]>([]);
    const [filteredAllPeople, setFilteredAllPeople] = useState<People[]>([]);
    const [, setPeoples] = useState<People[]>([]);
    const [filteredPeoples, setFilteredPeoples] = useState<People[]>([]);
    const [people, setPeople] = useState<People | undefined>(undefined);

    const [activePage, setActivePage] = useState<number>(1);
    const [pages, setPages] = useState<number>(0);
    const PAGES_TO_SHOW = 2;
    const REGISTERS_PER_PAGE = 5; // Es el mismo que params.limit

    // Filter state
    const [filters, setFilters] = useState<Record<string, string>>({
        search: "",
    });

    const getPeople = async () => {
        try {
            setLoading(true);
            const res = await getPeopleService<PeopleServiceData>(api, bParams);
            setPeoples(res.data);
            if (bParams.limit) {
                setPages(Math.ceil(res.total_count / bParams.limit));
            }
            res.data && res.data?.length === 0 && handlePage(1);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const getAllPeople = async () => {
        try {
            setLoading(true);
            const res = await getPeopleService<PeopleServiceData>(api);
            setAllPeople(res.data);
            setFilteredAllPeople(res.data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const paginatePeople = (page: number, filteredData: People[]) => {
        const start = REGISTERS_PER_PAGE * (page - 1);
        const end = start + REGISTERS_PER_PAGE;
        const paginatedData = filteredData.slice(start, end);

        setPeoples(paginatedData);
        setFilteredPeoples(paginatedData);
    };

    const handlePage = (page: number) => {
        if (page < 1 || page > pages) return;

        setActivePage(page);
        paginatePeople(page, filteredAllPeople);
    };

    const reFetch = () => {
        getPeople();
        getAllPeople();
    };

    useEffect(() => {
        if (!allPeople?.length) return;

        const searchTerm = filters.search.toLowerCase().trim();

        let filtered = [...allPeople];

        if (searchTerm) {
            filtered = allPeople.filter(
                (person) =>
                    person.first_name.toLowerCase().includes(searchTerm) ||
                    person.last_name.toLowerCase().includes(searchTerm) ||
                    person.email.toLowerCase().includes(searchTerm) ||
                    (person.user_name &&
                        person.user_name.toLowerCase().includes(searchTerm)) ||
                    (person.institution &&
                        person.institution
                            .toLowerCase()
                            .includes(searchTerm)) ||
                    (person.position &&
                        person.position.toLowerCase().includes(searchTerm)),
            );
        }

        setFilteredAllPeople(filtered);

        if (bParams.limit) {
            setPages(Math.ceil(filtered?.length / bParams.limit));
        }

        setActivePage(1);
        paginatePeople(1, filtered);
    }, [filters, allPeople, bParams.limit]);

    useEffect(() => {
        getAllPeople();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const titles = [
        "Username",
        "Name",
        "Lastname",
        "Email",
        "Phone",
        "Address",
        "Institution",
        "Position",
    ];

    const body = useMemo(() => {
        return filteredPeoples
            ?.sort((a, b) => a.first_name.localeCompare(b.first_name))
            .map((st) =>
                Object.values({
                    username: st.user_name,
                    name: st.first_name,
                    lastname: st.last_name,
                    email: st.email,
                    phone: st.phone,
                    address: st.address,
                    institution: st.institution,
                    position: st.position,
                }),
            );
    }, [filteredPeoples]);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    const handleFiltersChange = (newFilters: Record<string, string>) => {
        setFilters(newFilters);
    };

    return (
        <TableCard
            title={"People"}
            size={"1150px"}
            addButtonTitle="+ Person"
            modalTitle="EditPerson"
            setModals={setModals}
            addButton={true}
            secondAddButton={true}
            secondAddButtonTitle="Merge"
            secondModalTitle="MergePeople"
            filters={filters}
            setFilters={handleFiltersChange}
            showSearch={true}
            searchPlaceholder="Search by Name, Last Name..."
        >
            <Table
                titles={body && body?.length > 0 ? titles : []}
                body={body}
                table={"PeopleRelations"}
                loading={loading}
                dataOnly={false}
                onClickFunction={() =>
                    setModals({
                        show: true,
                        title: "EditPerson",
                        type: "edit",
                    })
                }
                setState={setPeople}
                state={filteredPeoples}
                viewRegister={true}
                onViewClickFunction={() => {
                    setModals({
                        show: true,
                        title: "ViewPersonRelations",
                        type: "none",
                    });
                }}
                dataFetchUrl="api/people"
            />
            {body && body?.length > 0 ? (
                <Pagination
                    pages={pages}
                    pagesToShow={PAGES_TO_SHOW}
                    activePage={activePage}
                    handlePage={handlePage}
                />
            ) : null}
            {modals?.show && modals.title === "EditPerson" && (
                <StationPeopleModal
                    Person={people}
                    people={allPeople}
                    modalType={modals.type}
                    setStateModal={setModals}
                    setPerson={setPeople}
                    reFetch={reFetch}
                />
            )}
            {modals?.show &&
                modals.title === "MergePeople" &&
                body &&
                body?.length > 0 && (
                    <MergePeopleModal
                        setStateModal={setModals}
                        handleCloseModal={() => setModals(undefined)}
                        body={allPeople as People[]}
                    />
                )}
            {modals?.show && modals.title === "ViewPersonRelations" && (
                <ViewPersonRelations
                    Person={people}
                    reFetch={reFetch}
                    setStateModal={setModals}
                />
            )}
        </TableCard>
    );
};

export default PeopleTable;

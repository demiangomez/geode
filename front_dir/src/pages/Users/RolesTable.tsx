import { useEffect, useMemo, useState } from "react";
import { RoleModal, Pagination, Table, TableCard } from "@componentsReact";

import { useAuth, useApi } from "@hooks";
import { getRolesService } from "@services";

import { GetParams, Role, RolesServiceData } from "@types";
import { showModal } from "@utils";

const RolesTable = () => {
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

    const [loading, setLoading] = useState<boolean>(false);

    const [roleParams, setRoleParams] = useState<GetParams>(bParams);

    const [roles, setRoles] = useState<Role[]>([]);
    const [role, setRole] = useState<Role | undefined>(undefined);

    const [activeRolePage, setActiveRolePage] = useState<number>(1);
    const [rolesPages, setRolesPages] = useState<number>(0);

    const PAGES_TO_SHOW = 2;
    const REGISTERS_PER_PAGE = 5; // Es el mismo que params.limit

    const getRoles = async () => {
        try {
            const res = await getRolesService<RolesServiceData>(
                api,
                roleParams,
            );
            setRoles(res.data);
            if (bParams.limit)
                setRolesPages(Math.ceil(res.total_count / bParams.limit));
        } catch (err) {
            console.error(err);
        }
    };

    const paginateRoles = async (newParams: GetParams) => {
        try {
            setLoading(true);
            const res = await getRolesService<RolesServiceData>(api, newParams);
            setRoles(res.data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleRolesPages = (page: number) => {
        if (page < 1 || page > rolesPages) return;
        let newParams;
        if (page === 1) {
            newParams = {
                ...roleParams,
                limit: REGISTERS_PER_PAGE * 1,
                offset: REGISTERS_PER_PAGE * (page - 1),
            };
        } else {
            newParams = {
                ...roleParams,
                limit: REGISTERS_PER_PAGE,
                offset: REGISTERS_PER_PAGE * (page - 1),
            };
        }

        setRoleParams(newParams);
        setActiveRolePage(page);
        paginateRoles(newParams);
    };

    useEffect(() => {
        getRoles();
    }, []); // eslint-disable-line

    const titles = ["Name", "Api Role", "All Endpoints Allowed", "Active"];
    const body = useMemo(() => {
        return roles?.map((role) =>
            Object.values({
                name: role.name,
                api_role: role.role_api,
                allow_all: role.allow_all,
                active: role.is_active,
            }),
        );
    }, [roles]);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    return (
        <TableCard
            title={"Roles"}
            addButton={true}
            addButtonTitle="+ Role"
            modalTitle={"AddRole"}
            setModals={setModals}
            size="606"
        >
            <Table
                titles={body && body.length > 0 ? titles : []}
                body={body}
                loading={loading}
                table={"Roles"}
                dataOnly={false}
                onClickFunction={() =>
                    setModals({
                        show: true,
                        title: "AddRole",
                        type: "edit",
                    })
                }
                setState={setRole}
                state={roles}
            />
            {body && body.length > 0 ? (
                <Pagination
                    pages={rolesPages}
                    pagesToShow={PAGES_TO_SHOW}
                    activePage={activeRolePage}
                    handlePage={handleRolesPages}
                />
            ) : null}

            {modals?.show && modals.title === "AddRole" ? (
                <RoleModal
                    Role={role}
                    modalType={modals.type}
                    reFetch={getRoles}
                    setRole={setRole}
                    setStateModal={setModals}
                />
            ) : null}
        </TableCard>
        // </div>
    );
};

export default RolesTable;

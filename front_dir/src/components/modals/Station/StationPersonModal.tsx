import { useEffect, useRef, useState } from "react";
import {
    Alert,
    ConfirmDeleteModal,
    Menu,
    MenuButton,
    MenuContent,
    Modal,
} from "@componentsReact";
import { useApi, useAuth, useFormReducer } from "@hooks";
import { apiOkStatuses, showModal } from "@utils";
import {
    ErrorResponse,
    Errors,
    ExtendedRolePersonStationData,
    People,
    RolePersonStationData,
    StationData,
    StationStatus,
} from "@types";
import {
    delRolePersonStationService,
    postRolePersonStationService,
} from "@services";

interface Props {
    people: People[] | undefined;
    roles: StationStatus[] | undefined;
    Person: RolePersonStationData | undefined;
    Station: StationData;
    modalType: string;
    reFetch: () => void;
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
}

const StationPersonModal = ({
    people,
    roles,
    Person,
    Station,
    modalType,
    reFetch,
    setStateModal,
}: Props) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const [matchingPeople, setMatchingPeople] = useState<People[] | undefined>(
        undefined,
    );

    const [matchingRoles, setMatchingRoles] = useState<
        StationStatus[] | undefined
    >(undefined);

    const [loading, setLoading] = useState<boolean>(false);
    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [showMenu, setShowMenu] = useState<
        { type: string; show: boolean } | undefined
    >(undefined);

    const { formState, dispatch } = useFormReducer({
        role: "",
        person: "",
        station: "",
    });

    const postPerson = async () => {
        try {
            setLoading(true);

            const data = {
                role: String(roles?.find((r) => r.name === formState.role)?.id),
                person: formState.person,
                station: String(Station.api_id),
            };
            const res = await postRolePersonStationService<
                ExtendedRolePersonStationData | ErrorResponse
            >(api, data);
            if ("status" in res) {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: "Person added successfully",
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const delPerson = async () => {
        try {
            setLoading(true);

            const res = await delRolePersonStationService<ErrorResponse>(
                api,
                Number(Person?.id),
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

    const handleCloseModal = () => {
        reFetch();
    };

    const handleChange = (
        e:
            | HTMLInputElement
            | HTMLSelectElement
            | { target: { name: string; value: string } },
    ) => {
        const { name, value } = "target" in e ? e.target : e;

        dispatch({
            type: "change_value",
            payload: {
                inputName: name,
                inputValue: value,
            },
        });

        if (name === "person") {
            const match = people?.filter((p) =>
                p.first_name.toLowerCase().includes(value.toLowerCase()),
            );

            setMatchingPeople(match);
            setShowMenu({ type: name, show: true });
        }

        if (name === "role") {
            setShowMenu({ type: name, show: true });
            const match = roles?.filter((r) =>
                r.name.toLowerCase().includes(value.toLowerCase().trim()),
            );
            setMatchingRoles(match);
        }
    };

    const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        if (modalType === "add") {
            postPerson();
        }
    };

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    const inputRefPerson = useRef<HTMLInputElement>(null);

    const inputRefRole = useRef<HTMLInputElement>(null);

    const selectRef = (key: string) => {
        return key === "role"
            ? inputRefRole
            : key === "person"
              ? inputRefPerson
              : null;
    };

    useEffect(() => {
        if (showMenu) {
            const ref = selectRef(showMenu.type);
            if (ref && ref.current) {
                ref.current.focus();
            }
        }
    }, [showMenu]);

    return (
        <Modal
            close={false}
            modalId={"EditStationPerson"}
            size={"smPlus"}
            handleCloseModal={() => handleCloseModal()}
            setModalState={setStateModal}
        >
            <div className="w-full flex grow mb-2">
                <h3 className="font-bold text-center text-2xl my-2 w-full self-center">
                    {modalType?.charAt(0).toUpperCase() + modalType?.slice(1)}
                </h3>
            </div>
            <form className="form-control space-y-4" onSubmit={handleSubmit}>
                <div className="form-control space-y-2">
                    {Object.keys(formState || {}).map((key, index) => {
                        const errorBadge = msg?.errors?.errors?.find(
                            (error) => error.attr === key,
                        );

                        const keysToMenu: string[] = ["person", "role"];

                        const person =
                            key === "person"
                                ? people?.find(
                                      (p) =>
                                          p.id ===
                                          Number(
                                              formState[
                                                  key as keyof typeof formState
                                              ],
                                          ),
                                  )
                                : null;

                        // const optionalFields: string[] = ["photo", "user"];
                        return (
                            <div className="flex flex-col" key={key + index}>
                                {key !== "station" && (
                                    <>
                                        <label
                                            key={index}
                                            id={key}
                                            className={`w-full input input-bordered flex items-center gap-2 ${errorBadge ? "input-error" : ""}`}
                                            title={
                                                errorBadge
                                                    ? errorBadge.detail
                                                    : ""
                                            }
                                        >
                                            <div className="label">
                                                <span className="font-bold">
                                                    {key
                                                        .toUpperCase()
                                                        .replace("_", " ")
                                                        .replace("_", " ")}
                                                </span>
                                            </div>
                                            <input
                                                type="text"
                                                name={key}
                                                ref={selectRef(key)}
                                                value={
                                                    key === "person" && person
                                                        ? person?.first_name +
                                                          " " +
                                                          person?.last_name
                                                        : (formState[
                                                              key as keyof typeof formState
                                                          ] ?? "")
                                                }
                                                onChange={(e) => {
                                                    handleChange(e.target);
                                                    setShowMenu({
                                                        type: key,
                                                        show: true,
                                                    });
                                                }}
                                                onClick={(e) => {
                                                    handleChange(
                                                        e.target as HTMLInputElement,
                                                    );
                                                    setShowMenu({
                                                        type: key,
                                                        show: true,
                                                    });
                                                }}
                                                className="grow"
                                                autoComplete="off"
                                                disabled={key === "id"}
                                            />
                                            {errorBadge && (
                                                <span className="badge badge-error">
                                                    {errorBadge.code}
                                                </span>
                                            )}
                                            {keysToMenu.includes(key) && (
                                                <MenuButton
                                                    setShowMenu={setShowMenu}
                                                    onMenuClick={() =>
                                                        handleChange({
                                                            target: {
                                                                name: key,
                                                                value: formState[
                                                                    key as keyof typeof formState
                                                                ],
                                                            },
                                                        })
                                                    }
                                                    showMenu={showMenu}
                                                    typeKey={key}
                                                />
                                            )}
                                        </label>
                                        {showMenu?.show &&
                                        showMenu.type === key &&
                                        key === "person" ? (
                                            <Menu>
                                                {(matchingPeople &&
                                                matchingPeople.length > 0
                                                    ? matchingPeople
                                                    : people
                                                )?.map((p) => (
                                                    <MenuContent
                                                        key={p.id}
                                                        typeKey={key}
                                                        value={
                                                            p.first_name +
                                                            " " +
                                                            p.last_name
                                                        }
                                                        alterValue={String(
                                                            p.id,
                                                        )}
                                                        dispatch={dispatch}
                                                        setShowMenu={
                                                            setShowMenu
                                                        }
                                                    />
                                                ))}
                                            </Menu>
                                        ) : (
                                            showMenu?.show &&
                                            showMenu.type === key &&
                                            key === "role" && (
                                                <Menu>
                                                    {(matchingRoles &&
                                                    matchingRoles.length > 0
                                                        ? matchingRoles
                                                        : roles
                                                    )?.map((r) => (
                                                        <MenuContent
                                                            key={r.id}
                                                            typeKey={key}
                                                            value={r.name}
                                                            // alterValue={String(p.id)}
                                                            dispatch={dispatch}
                                                            setShowMenu={
                                                                setShowMenu
                                                            }
                                                        />
                                                    ))}
                                                </Menu>
                                            )
                                        )}
                                    </>
                                )}
                            </div>
                        );
                    })}
                </div>
                <Alert msg={msg} />
                {loading && (
                    <div className="w-full text-center">
                        <span className="loading loading-spinner loading-lg self-center"></span>
                    </div>
                )}
                <div className="flex w-full justify-center space-x-4">
                    {modalType === "edit" && (
                        <button
                            className="btn btn-error w-5/12"
                            type="button"
                            disabled={
                                apiOkStatuses.includes(Number(msg?.status)) ||
                                loading
                            }
                            onClick={() =>
                                setModals({
                                    show: true,
                                    title: "ConfirmDelete",
                                    type: "edit",
                                })
                            }
                        >
                            Remove
                        </button>
                    )}
                    <button
                        type="submit"
                        className="btn btn-success w-5/12"
                        disabled={
                            apiOkStatuses.includes(Number(msg?.status)) ||
                            loading
                        }
                    >
                        Submit
                    </button>
                </div>
            </form>
            {modals && modals?.title === "ConfirmDelete" && (
                <ConfirmDeleteModal
                    msg={msg}
                    loading={loading}
                    confirmRemove={() => delPerson()}
                    closeModal={() => {
                        setModals({
                            show: false,
                            title: "",
                            type: "edit",
                        });
                        setMsg(undefined);
                    }}
                />
            )}
        </Modal>
    );
};

export default StationPersonModal;

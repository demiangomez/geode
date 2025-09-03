import { useEffect, useRef, useState } from "react";
import {
    Alert,
    ConfirmDeleteModal,
    MenuButton,
    Menu,
    MenuContent,
    Modal,
} from "@componentsReact";
import {
    delPeopleService,
    getUsersService,
    patchPeopleService,
    postPeopleService,
} from "@services";
import { useAuth, useApi, useFormReducer } from "@hooks";
import { apiOkStatuses, showModal } from "@utils";
import {
    Errors,
    ErrorResponse,
    People,
    ExtendedPeople,
    UsersData,
    UsersServiceData,
} from "@types";

interface Props {
    Person: People | undefined;
    people?: People[];
    modalType: string;
    reFetch: () => void;
    setStateModal?: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
    setPerson?: React.Dispatch<React.SetStateAction<People | undefined>>;
}

const StationPeopleModal = ({
    Person,
    people,
    modalType,
    reFetch,
    setStateModal,
    setPerson,
}: Props) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const [duplicateError, setDuplicateError] = useState<string | undefined>(
        undefined,
    );

    const [matchingUsers, setMatchingUsers] = useState<UsersData[] | undefined>(
        undefined,
    );

    const isDuplicate = (first: string, last: string) => {
        if (!people || people.length === 0) return false;
        const f = (first ?? "").trim().toLowerCase();
        const l = (last ?? "").trim().toLowerCase();
        return people.some((p) => {
            const pf = (p.first_name ?? "").trim().toLowerCase();
            const pl = (p.last_name ?? "").trim().toLowerCase();
            const samePerson = Person && Number(Person.id) === Number(p.id);
            return pf === f && pl === l && !samePerson;
        });
    };

    const [loading, setLoading] = useState<boolean>(false);
    const [msg, setMsg] = useState<
        | {
              status: number;
              msg: string;
              errors?: Errors;
              rinex_other_errors?: { [key: string]: string[] } | undefined;
          }
        | undefined
    >(undefined);

    const [users, setUsers] = useState<UsersData[]>([]);

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [checks, setChecks] = useState<{ photo: boolean }>({ photo: false });

    const [showMenu, setShowMenu] = useState<
        { type: string; show: boolean } | undefined
    >(undefined);

    const { formState, dispatch } = useFormReducer({
        id: "",
        first_name: "",
        last_name: "",
        email: "",
        address: "",
        phone: "",
        photo_actual_file: "",
        user: null,
        institution: "",
        position: "",
    });

    const getUsers = async () => {
        try {
            const res = await getUsersService<UsersServiceData>(api);
            setUsers(res.data);
        } catch (err) {
            console.error(err);
        }
    };

    useEffect(() => {
        if (Person) {
            const { user_name, ...rest } = Person;

            rest.user = user_name;

            dispatch({
                type: "set",
                payload: rest,
            });
        }
    }, [Person]); // eslint-disable-line

    const postPerson = async () => {
        try {
            setLoading(true);

            const { id, photo_actual_file, user, ...data } = formState; // eslint-disable-line

            const formData = new FormData();

            Object.keys(data).forEach((key) => {
                formData.append(key, data[key as keyof typeof data]);
            });

            if (photo_actual_file) {
                formData.append("photo", photo_actual_file);
            }

            const userById = users?.find((u) => u.username === user);

            if (userById) {
                formData.append("user", String(userById.id));
            }
            const res = await postPeopleService<ExtendedPeople | ErrorResponse>(
                api,
                formData,
            );
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

    const patchPerson = async () => {
        try {
            setLoading(true);

            const { id, user, photo_actual_file, ...data } = formState; // eslint-disable-line

            const formData = new FormData();

            Object.keys(data).forEach((key) => {
                formData.append(key, data[key as keyof typeof data]);
            });

            if (photo_actual_file) {
                formData.append("photo", photo_actual_file);
            }

            const userById = users?.find((u) => u.username === user);

            if (userById) {
                formData.append("user", String(userById.id));
            }

            if (!checks?.photo) {
                formData.delete("photo");
            }

            if (formState.user === "") {
                formData.append("user", "");
            }

            const res = await patchPeopleService<
                ExtendedPeople | ErrorResponse
            >(api, Number(Person?.id), formData);
            if ("status" in res) {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: "Person edited successfully",
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

            const res = await delPeopleService<ErrorResponse>(
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
        setPerson?.(undefined);
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

        if (name === "user") {
            const match = users?.filter((u) =>
                u.username.toLowerCase().includes(value.toLowerCase()),
            );
            setMatchingUsers(match);
            setShowMenu({
                type: name,
                show: true,
            });
        }

        // check duplicate when first_name or last_name changes
        if (name === "first_name" || name === "last_name") {
            const newFirst =
                name === "first_name"
                    ? value
                    : ((formState.first_name as string) ?? "");
            const newLast =
                name === "last_name"
                    ? value
                    : ((formState.last_name as string) ?? "");

            // mensaje más descriptivo para mostrar en el badge y en el modal de confirmación
            setDuplicateError(
                isDuplicate(newFirst, newLast) ? "Warning" : undefined,
            );
        }
    };

    const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        if (duplicateError) {
            setModals({
                show: true,
                title: "ConfirmDuplicate",
                type: modalType === "edit" ? "edit" : "add",
            });
            return;
        }

        if (modalType === "edit") {
            patchPerson();
        } else if (modalType === "add") {
            postPerson();
        }
    };

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    useEffect(() => {
        getUsers();
    }, []);

    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (showMenu) {
            inputRef.current?.focus();
        }
    }, [showMenu]);

    return (
        <Modal
            close={true}
            modalId={"EditPerson"}
            size={"smPlus"}
            handleCloseModal={() => handleCloseModal()}
            setModalState={setStateModal}
        >
            <div className="w-full flex grow mb-2">
                <h3 className="font-bold text-center text-2xl my-2 w-full self-center">
                    {modalType?.charAt(0).toUpperCase() + modalType?.slice(1)}
                </h3>
            </div>
            <form
                className="form-control space-y-4"
                onSubmit={(e) => handleSubmit(e)}
            >
                <div className="form-control space-y-2">
                    {Object.keys(formState || {}).map((key, index) => {
                        const errorBadge = msg?.errors?.errors?.find(
                            (error) => error.attr === key,
                        );
                        const optionalFields: string[] = [
                            "photo_actual_file",
                            "user",
                        ];

                        const isNameDup =
                            !!duplicateError &&
                            (key === "first_name" || key === "last_name");
                        return (
                            <div className="flex flex-col" key={key + index}>
                                {key === "photo_actual_file" ? (
                                    <div className="flex gap-2 items-center w-full">
                                        <input
                                            type="file"
                                            className="file-input file-input-bordered w-full"
                                            disabled={
                                                modalType === "edit" &&
                                                !checks?.photo
                                            }
                                            onChange={(e) => {
                                                dispatch({
                                                    type: "change_value",
                                                    payload: {
                                                        inputName:
                                                            "photo_actual_file",
                                                        inputValue:
                                                            e.target.files &&
                                                            e.target.files
                                                                .length > 0
                                                                ? e.target
                                                                      .files[0]
                                                                : undefined,
                                                    },
                                                });
                                            }}
                                        />
                                        {modalType === "edit" && (
                                            <input
                                                type="checkbox"
                                                className="checkbox"
                                                title={"Check to change photo"}
                                                checked={checks?.photo}
                                                onChange={() =>
                                                    setChecks((prev) => ({
                                                        ...prev,
                                                        photo: !prev?.photo,
                                                    }))
                                                }
                                            />
                                        )}
                                        {optionalFields.includes(key) && (
                                            <span className="badge badge-secondary">
                                                Optional
                                            </span>
                                        )}
                                    </div>
                                ) : (
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
                                                ref={
                                                    key === "user"
                                                        ? inputRef
                                                        : null
                                                }
                                                name={key}
                                                value={
                                                    formState[
                                                        key as keyof typeof formState
                                                    ] ?? ""
                                                }
                                                onChange={(e) => {
                                                    handleChange(e.target);
                                                }}
                                                onClick={() => {
                                                    if (key === "user") {
                                                        setShowMenu({
                                                            type: key,
                                                            show: true,
                                                        });
                                                    } else {
                                                        setShowMenu(undefined);
                                                    }
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
                                            {isNameDup && (
                                                <span
                                                    className="badge badge-warning ml-2 text-warning-content"
                                                    title="A person with that first and last name is already registered."
                                                    role="status"
                                                    aria-label="Advertencia: nombre duplicado"
                                                >
                                                    {duplicateError}
                                                </span>
                                            )}
                                            {key === "user" && (
                                                <MenuButton
                                                    setShowMenu={setShowMenu}
                                                    onMenuClick={() =>
                                                        handleChange({
                                                            target: {
                                                                name: key,
                                                                value:
                                                                    formState[
                                                                        key as keyof typeof formState
                                                                    ] ?? "",
                                                            },
                                                        })
                                                    }
                                                    showMenu={showMenu}
                                                    typeKey={key}
                                                />
                                            )}
                                            {optionalFields.includes(key) && (
                                                <span className="badge badge-secondary">
                                                    Optional
                                                </span>
                                            )}
                                        </label>
                                        {showMenu?.show &&
                                        showMenu.type === key &&
                                        key === "user" ? (
                                            <Menu>
                                                {(matchingUsers &&
                                                matchingUsers.length > 0
                                                    ? matchingUsers
                                                    : users
                                                )?.map((u) => (
                                                    <MenuContent
                                                        key={u.id}
                                                        typeKey={key}
                                                        value={u.username}
                                                        dispatch={dispatch}
                                                        setShowMenu={
                                                            setShowMenu
                                                        }
                                                    />
                                                ))}
                                            </Menu>
                                        ) : null}
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
                    }}
                />
            )}
            {modals && modals?.title === "ConfirmDuplicate" && (
                <Modal
                    close={true}
                    modalId={"ConfirmDuplicate"}
                    size={"sm"}
                    handleCloseModal={() =>
                        setModals({ show: false, title: "", type: "edit" })
                    }
                    setModalState={setModals}
                >
                    <div className="w-full">
                        <h3 className="font-bold text-xl mb-2 text-center">
                            Duplicated
                        </h3>
                        <p className="text-md">
                            A user with that name is already in our system.
                        </p>
                        <p className="text-md mt-2">
                            Would you like to proceed anyway?
                        </p>
                        <div className="flex justify-end gap-2 mt-4">
                            <button
                                type="button"
                                className="btn"
                                onClick={() =>
                                    setModals({
                                        show: false,
                                        title: "",
                                        type: "edit",
                                    })
                                }
                                disabled={loading}
                            >
                                Cancel
                            </button>
                            <button
                                type="button"
                                className="btn btn-warning"
                                onClick={async () => {
                                    setModals({
                                        show: false,
                                        title: "",
                                        type: "edit",
                                    });
                                    if (modalType === "edit") {
                                        await patchPerson();
                                    } else {
                                        await postPerson();
                                    }
                                }}
                                disabled={loading}
                            >
                                Confirm
                            </button>
                        </div>
                    </div>
                </Modal>
            )}
        </Modal>
    );
};

export default StationPeopleModal;

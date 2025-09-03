import { useEffect, useState } from "react";
import { Alert, ConfirmDeleteModal, Modal } from "@componentsReact";

import { EyeIcon, EyeSlashIcon } from "@heroicons/react/24/outline";

import { useApi, useAuth, useFormReducer } from "@hooks/index";
import { USERS_STATE } from "@utils/reducerFormStates";
import { apiOkStatuses, jwtDeserializer, showModal } from "@utils";

import {
    getRolesService,
    postUserService,
    patchUserService,
    getUserPhotoService,
    deleteUserService,
} from "@services";

import {
    ErrorResponse,
    Errors,
    ExtendedUsersData,
    Role,
    RolesServiceData,
    User,
    UsersData,
} from "@types";

interface EditUsersModalProps {
    User: UsersData | undefined;
    modalType: "add" | "edit" | "none";
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
    setUser: React.Dispatch<React.SetStateAction<UsersData | undefined>>;
    reFetch: () => void;
}

const EditUsersModal = ({
    User,
    modalType,
    setStateModal,
    setUser,
    reFetch,
}: EditUsersModalProps) => {
    const { token, logout, getUserPhoto } = useAuth();
    const api = useApi(token, logout);

    const tokenDeserialized = jwtDeserializer(token ?? "");

    const { formState, dispatch } = useFormReducer(USERS_STATE);
    type FormStateWithoutRole = Omit<typeof formState, "role">;

    const [roles, setRoles] = useState<Role[]>([]);

    const [loadingSubmit, setLoadingSubmit] = useState<boolean>(false);
    const [loadingDelete, setLoadingDelete] = useState<boolean>(false);
    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);
    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [seePwd, setSeePwd] = useState<boolean>(false);

    const [checks, setChecks] = useState<{ photo: boolean; password: boolean }>(
        { photo: false, password: false },
    );

    const [userPhoto, setUserPhoto] = useState<string | null>(null);

    useEffect(() => {
        if (User) {
            const extendedUser = {
                id: User.id,
                username: User.username,
                password: "",
                role: {
                    id: User.role.id,
                    name: User.role.name,
                },
                is_active: User.is_active,
                first_name: User.first_name,
                last_name: User.last_name,
                email: User.email,
                phone: User.phone,
                address: User.address,
                photo: User.photo,
            };
            dispatch({
                type: "set",
                payload: extendedUser,
            });
        }
    }, [User]); // eslint-disable-line

    const confirmDeleteUser = () => {
        setModals({
            show: true,
            title: "ConfirmDelete",
            type: "edit",
        });
    };

    const handleChange = (e: HTMLInputElement | HTMLSelectElement) => {
        const { name, value } = e;

        dispatch({
            type: "change_value",
            payload: {
                inputName: name,
                inputValue: value,
            },
        });
    };

    const getRoles = async () => {
        try {
            const res = await getRolesService<RolesServiceData>(api);
            setRoles(res.data);
        } catch (err) {
            console.error(err);
        }
    };

    const getPhoto = async () => {
        try {
            const res = await getUserPhotoService<any>(api, Number(User?.id));
            //const url = URL.createObjectURL(res);
            const url = "data:image/*;base64," + res.photo;
            setUserPhoto(url);
        } catch (err) {
            console.error(err);
        }
    };

    const postUser = async () => {
        try {
            setLoadingSubmit(true);
            setMsg(undefined);

            const { role, photo, ...rest } = formState;

            const formData = new FormData();
            if (photo) {
                formData.append("photo", photo);
            }

            Object.keys(rest).forEach((key) => {
                formData.append(key, rest[key]);
            });

            formData.append("role", String(role.id));

            const res = await postUserService<
                ExtendedUsersData | ErrorResponse
            >(api, formData);
            if (res) {
                if ("status" in res) {
                    setMsg({
                        status: res.statusCode,
                        msg: res.response.type,
                        errors: res.response,
                    });
                } else {
                    setMsg({
                        status: res.statusCode,
                        msg: "User added successfully",
                    });
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoadingSubmit(false);
        }
    };

    const deleteUser = async () => {
        try {
            setLoadingDelete(true);
            setMsg(undefined);

            if (!formState.id) {
                setMsg({
                    status: 400,
                    msg: "No user ID provided",
                });
                return;
            }

            const res = await deleteUserService<ErrorResponse>(
                api,
                formState.id,
            );

            if (res) {
                if (res.status == "success") {
                    setMsg({
                        status: res.statusCode,
                        msg: "User deleted successfully",
                    });
                } else {
                    setMsg({
                        status: res.statusCode,
                        msg: res.response.type,
                        errors: res.response,
                    });
                }
            }
        } catch (err: any) {
            console.error(err);

            // Handle different types of errors
            if (err?.response?.status) {
                setMsg({
                    status: err.response.status,
                    msg: err.response.data?.message || "Error deleting user",
                });
            } else {
                setMsg({
                    status: 500,
                    msg: "Error deleting user",
                });
            }
        } finally {
            setLoadingDelete(false);
        }
    };

    const putUser = async () => {
        try {
            setLoadingSubmit(true);
            setMsg(undefined);

            const { role, password, photo, ...rest } = formState;

            const formData = new FormData();

            formData.append("photo", photo);

            if (!checks?.photo) {
                formData.delete("photo");
            }

            if (password && checks?.password) {
                formData.append("password", password);
            }

            Object.keys(rest).forEach((key) => {
                formData.append(key, rest[key]);
            });

            formData.append("role", String(role.id));

            const res = await patchUserService<User | ErrorResponse>(
                api,
                Number(formState.id),
                formData,
            );
            if (res) {
                if ("status" in res) {
                    setMsg({
                        status: res.statusCode,
                        msg: res.response.type,
                        errors: res.response,
                    });
                } else {
                    setMsg({
                        status: 200,
                        msg: "User updated successfully",
                    });
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoadingSubmit(false);
        }
    };

    const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        if (modalType === "edit") {
            putUser();
        } else if (modalType === "add") {
            postUser();
        }
    };

    useEffect(() => {
        getRoles();
        if (modalType === "edit") {
            getPhoto();
        }
    }, []); // eslint-disable-line

    useEffect(() => {
        setLoadingDelete(true);
        modals?.show && showModal(modals.title);
        setLoadingDelete(false);
    }, [modals]);

    return (
        <Modal
            close={false}
            modalId={"EditUsers"}
            size={"md"}
            handleCloseModal={() => {
                setUser(undefined);
                if (tokenDeserialized?.user_id === User?.id) {
                    getUserPhoto();
                }
                reFetch();
            }}
            setModalState={setStateModal}
        >
            <div className="w-full flex grow mb-2">
                <h3 className="font-bold text-center text-2xl my-2 w-full self-center">
                    {modalType.charAt(0).toUpperCase() + modalType.slice(1)}
                </h3>
                {userPhoto && (
                    <div className="flex">
                        <div className="card">
                            <div className="badge badge-accent gap-2 self-end -mb-4 z-[1]">
                                userphoto
                            </div>
                            <img
                                className="mask mask-circle w-[7rem] max-w-[10rem]"
                                src={userPhoto ?? ""}
                            />
                        </div>
                    </div>
                )}
            </div>
            <form
                className="form-control space-y-4"
                onSubmit={(e: React.FormEvent<HTMLFormElement>) =>
                    handleSubmit(e)
                }
            >
                <div className="form-control space-y-2">
                    {Object.keys(formState || {}).map((key, index) => {
                        const noOptionalFields = [
                            "id",
                            "username",
                            "password",
                            "is_active",
                        ];
                        const errorBadge = msg?.errors?.errors?.find(
                            (error) => error.attr === key,
                        );
                        return (
                            <div key={index} className="flex flex-col">
                                {key === "role" ? (
                                    <select
                                        value={formState.role.id || ""}
                                        className="select select-bordered w-full text-center font-bold"
                                        onChange={(e) => {
                                            dispatch({
                                                type: "change_value",
                                                payload: {
                                                    inputName: "role.id",
                                                    inputValue: e.target.value,
                                                },
                                            });
                                            dispatch({
                                                type: "change_value",
                                                payload: {
                                                    inputName: "role.name",
                                                    inputValue: roles.find(
                                                        (role) =>
                                                            role.id ===
                                                            Number(
                                                                e.target.value,
                                                            ),
                                                    )?.name,
                                                },
                                            });
                                        }}
                                    >
                                        <option disabled value="">
                                            Select a role
                                        </option>
                                        {roles?.map((role) => (
                                            <option
                                                key={role.id}
                                                value={role.id}
                                            >
                                                {role.name.toUpperCase()}
                                            </option>
                                        ))}
                                    </select>
                                ) : key === "is_active" ? (
                                    <select
                                        value={
                                            formState.is_active !== null
                                                ? formState.is_active.toString()
                                                : ""
                                        }
                                        className="select select-bordered w-full text-center font-bold"
                                        onChange={(e) => {
                                            dispatch({
                                                type: "change_value",
                                                payload: {
                                                    inputName: "is_active",
                                                    inputValue:
                                                        e.target.value ===
                                                        "true"
                                                            ? true
                                                            : false,
                                                },
                                            });
                                        }}
                                    >
                                        <option disabled value="">
                                            Select a status
                                        </option>
                                        <option value="true">ACTIVE</option>
                                        <option value="false">INACTIVE</option>
                                    </select>
                                ) : (
                                    <div className="flex flex-col">
                                        {errorBadge && (
                                            <div className="badge badge-error gap-2 self-end -mb-2 z-[1]">
                                                {errorBadge.code.toUpperCase()}
                                            </div>
                                        )}
                                        {key === "photo" ? (
                                            <div>
                                                <div className="flex gap-2 items-center ">
                                                    <input
                                                        type="file"
                                                        className="file-input file-input-bordered w-full"
                                                        disabled={
                                                            modalType ===
                                                                "edit" &&
                                                            !checks?.photo
                                                        }
                                                        onChange={(e) => {
                                                            dispatch({
                                                                type: "change_value",
                                                                payload: {
                                                                    inputName:
                                                                        "photo",
                                                                    inputValue:
                                                                        e.target
                                                                            .files &&
                                                                        e.target
                                                                            .files
                                                                            .length >
                                                                            0
                                                                            ? e
                                                                                  .target
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
                                                            title={
                                                                "Check to change photo"
                                                            }
                                                            checked={
                                                                checks?.photo
                                                            }
                                                            onChange={() =>
                                                                setChecks(
                                                                    (prev) => ({
                                                                        ...prev,
                                                                        photo: !prev?.photo,
                                                                    }),
                                                                )
                                                            }
                                                        />
                                                    )}
                                                </div>
                                            </div>
                                        ) : (
                                            <div className="flex items-center gap-2">
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
                                                                .replace(
                                                                    "_",
                                                                    " ",
                                                                )
                                                                .replace(
                                                                    "_",
                                                                    " ",
                                                                )}
                                                        </span>
                                                    </div>
                                                    <input
                                                        type={`${key === "password" && (modalType !== "edit" ? !seePwd : !seePwd || !checks.password) ? "password" : "text"}`}
                                                        name={key}
                                                        value={
                                                            formState[
                                                                key as keyof FormStateWithoutRole
                                                            ] ?? ""
                                                        }
                                                        onChange={(e) => {
                                                            handleChange(
                                                                e.target,
                                                            );
                                                        }}
                                                        className="grow "
                                                        autoComplete="off"
                                                        disabled={
                                                            key === "id" ||
                                                            (modalType ===
                                                                "edit" &&
                                                                key ===
                                                                    "password" &&
                                                                !checks?.password)
                                                        }
                                                        placeholder={
                                                            key ===
                                                                "password" &&
                                                            !checks.password
                                                                ? "********"
                                                                : ""
                                                        }
                                                    />
                                                    {key === "password" ? (
                                                        (
                                                            modalType !== "edit"
                                                                ? seePwd
                                                                : seePwd &&
                                                                  checks.password
                                                        ) ? (
                                                            <EyeSlashIcon
                                                                className="size-6 cursor-pointer"
                                                                onClick={() =>
                                                                    setSeePwd(
                                                                        !seePwd,
                                                                    )
                                                                }
                                                            />
                                                        ) : (
                                                            <EyeIcon
                                                                className="size-6 cursor-pointer"
                                                                onClick={() =>
                                                                    setSeePwd(
                                                                        !seePwd,
                                                                    )
                                                                }
                                                            />
                                                        )
                                                    ) : null}
                                                    {!noOptionalFields.includes(
                                                        key,
                                                    ) && (
                                                        <span className="badge badge-secondary">
                                                            Optional
                                                        </span>
                                                    )}
                                                </label>
                                                {key === "password" &&
                                                    modalType === "edit" && (
                                                        <input
                                                            type="checkbox"
                                                            className="checkbox"
                                                            title={
                                                                "Check to change password"
                                                            }
                                                            checked={
                                                                checks?.password
                                                            }
                                                            onChange={() =>
                                                                setChecks(
                                                                    (prev) => ({
                                                                        ...prev,
                                                                        password:
                                                                            !prev?.password,
                                                                    }),
                                                                )
                                                            }
                                                        />
                                                    )}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
                {modals && modals?.title === "ConfirmDelete" && (
                    <ConfirmDeleteModal
                        msg={msg}
                        loading={loadingDelete}
                        confirmRemove={() => deleteUser()}
                        closeModal={() => {
                            setModals({
                                show: false,
                                title: "",
                                type: "edit",
                            });
                        }}
                    />
                )}
                {modals?.title !== "ConfirmDelete" && <Alert msg={msg} />}
                <div className="flex w-full justify-center space-x-4">
                    {modalType === "edit" && (
                        <button
                            type="button"
                            className="btn btn-error flex-1 max-w-[200px]"
                            disabled={
                                apiOkStatuses.includes(Number(msg?.status)) ||
                                loadingSubmit ||
                                loadingDelete
                            }
                            onClick={() => {
                                confirmDeleteUser();
                                setMsg(undefined);
                            }}
                        >
                            Remove
                        </button>
                    )}
                    <button
                        type="submit"
                        className={`btn btn-success flex-1 max-w-[200px] ${modalType === "add" ? "mx-auto" : ""}`}
                        disabled={
                            apiOkStatuses.includes(Number(msg?.status)) ||
                            loadingSubmit ||
                            loadingDelete
                        }
                    >
                        {loadingSubmit && (
                            <span className="loading loading-spinner loading-md"></span>
                        )}
                        Submit
                    </button>
                </div>
            </form>
        </Modal>
    );
};

export default EditUsersModal;

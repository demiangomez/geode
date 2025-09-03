import { Alert, ImageUploadCircle, Slider } from "@componentsReact";

import {
    EyeIcon,
    EyeSlashIcon,
    PencilSquareIcon,
} from "@heroicons/react/24/outline";

import { useApi, useAuth, useFormReducer } from "@hooks";

import { useEffect, useState, Fragment } from "react";

import { getRolesService, patchUserService } from "@services";

import {
    User,
    Role,
    Errors,
    UsersData,
    ErrorResponse,
    RolesServiceData,
} from "@types";

import { USERS_STATE } from "@utils/reducerFormStates";
import { apiOkStatuses } from "@utils";

type Props = {
    userData: UsersData | null;
    getData: () => void;
};

const UserSettingsForm = ({ userData, getData }: Props) => {
    const { formState, dispatch } = useFormReducer(USERS_STATE);
    const [initialValue, setInitialValue] = useState<UsersData>(
        USERS_STATE as UsersData,
    );
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const [roles, setRoles] = useState<Role[]>([]);
    const [image, setImagen] = useState<string | null>(null);
    const [edit, setEdit] = useState<boolean>(false);
    const [hasImage, setHasImage] = useState(false);

    const [saved, setSaved] = useState<boolean>(false);
    const [seePwd, setSeePwd] = useState<boolean>(false);
    const [loading, setLoading] = useState<boolean>(false);
    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const errorBadge = msg?.errors?.errors?.map((e) => e.attr);

    const optionalFields = [
        "first_name",
        "last_name",
        "role",
        "email",
        "phone",
        "address",
    ];

    // Retorna un string remplazando los _ por espacios y en mayuscula o minuscula
    const translateKey = (key: string, minus: boolean) => {
        if (minus) {
            const formatted = key.toLowerCase().replace(/_/g, " ");
            return formatted.charAt(0).toUpperCase() + formatted.slice(1);
        } else {
            return key.toUpperCase().replace("_", " ").replace("_", " ");
        }
    };

    const getRoles = async () => {
        try {
            const res = await getRolesService<RolesServiceData>(api);
            setRoles(res.data);
        } catch (err) {
            console.error(err);
        }
    };

    //with_people
    const putUser = async () => {
        try {
            setLoading(true);

            const formData = new FormData();

            const { role, photo, ...rest } = formState;

            Object.keys(rest).forEach((key) => {
                formData.append(key, rest[key]);
            });

            formData.append("role", String(role.id));

            // Manejar la imagen de forma especial
            if (image) {
                // Convertir base64 a File si es necesario
                const response = await fetch(image);
                const blob = await response.blob();
                const file = new File([blob], "profile-photo.jpg", {
                    type: "image/jpeg",
                });
                formData.append("photo", file);
            } else {
                formData.append("photo_actual_file", photo);
            }

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
                    setSaved(true);
                    setTimeout(() => {
                        getData();
                    }, 100);
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setEdit(false);
            setLoading(false);
        }
    };

    const handleChange = (e: HTMLInputElement | HTMLSelectElement) => {
        const { name, value } = e;
        const alterValue = () => {
            switch (value) {
                case "true":
                    return true;
                case "false":
                    return false;
                default:
                    return value;
            }
        };

        dispatch({
            type: "change_value",
            payload: {
                inputName: name,
                inputValue: alterValue(),
            },
        });
    };

    useEffect(() => {
        if (userData) {
            const initialValue = {
                id: userData.id,
                username: userData.username,
                password: userData.password,
                first_name: userData.first_name,
                last_name: userData.last_name,
                role: {
                    id: userData.role.id,
                    name: userData.role.name,
                },
                is_active: userData.is_active,
                email: userData.email,
                phone: userData.phone,
                address: userData.address,
                photo: userData.photo,
                clustering_distance: userData.clustering_distance,
            };

            setInitialValue(initialValue);

            dispatch({
                type: "set",
                payload: initialValue,
            });
        }
    }, [userData]);

    useEffect(() => {
        getRoles();
    }, []);

    useEffect(() => {
        if (!image) return;
        dispatch({
            type: "change_value",
            payload: {
                inputName: "photo",
                inputValue: image,
            },
        });
    }, [image]);

    const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        putUser();
    };

    return (
        <div className="w-full flex flex-col justify-center h-full">
            <div className="bg-base-200 px-6 h-full rounded-md align-middle">
                <header className="flex justify-between">
                    <h2 className="text-2xl font-bold my-3">User</h2>
                    <button
                        className="flex btn btn-ghost btn-circle self-center"
                        onClick={() => {
                            if (edit && !saved) {
                                dispatch({
                                    type: "set",
                                    payload: initialValue,
                                });
                                setImagen(null);
                            }

                            setEdit(!edit);
                            setMsg(undefined);
                            setHasImage(false);
                        }}
                    >
                        <PencilSquareIcon title="edit" className="size-8" />
                    </button>
                </header>
                <form
                    className="flex flex-col items-center justify-center w-full mb-3 "
                    onSubmit={handleSubmit}
                >
                    <ImageUploadCircle
                        edit={edit}
                        hasImage={hasImage}
                        image={
                            !edit
                                ? saved
                                    ? (formState.photo ?? null)
                                    : (initialValue.photo ?? null)
                                : (image ?? formState.photo)
                        }
                        setImage={setImagen}
                        setHasImage={setHasImage}
                    />
                    <div className="w-full grid grid-cols-2 gap-2">
                        {Object.keys(formState).map((key) => {
                            const notShow = ["photo", "id", "is_active"];
                            const doubleRow = [
                                "first_name",
                                "last_name",
                                "role",
                                "email",
                                "phone",
                                "address",
                                "clustering_distance",
                            ];

                            if (!notShow.includes(key))
                                return (
                                    <Fragment key={key}>
                                        {key === "role" ? (
                                            <select
                                                key={key}
                                                name={
                                                    key === "role"
                                                        ? "role.id"
                                                        : key
                                                }
                                                className={`select select-bordered w-full text-center font-bold col-span-${doubleRow.includes(key) ? "2" : "1"}`}
                                                disabled={!edit}
                                                onChange={(e) => {
                                                    handleChange(e.target);
                                                }}
                                                value={
                                                    key === "role" &&
                                                    formState.role.id
                                                        ? formState.role.id
                                                        : ""
                                                }
                                            >
                                                <option disabled value="">
                                                    {formState.role.name.toLocaleUpperCase() ??
                                                        "Select a role"}
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
                                        ) : key === "clustering_distance" ? (
                                            <>
                                                <Slider
                                                    tittle="CLUSTERING"
                                                    minValue={0}
                                                    maxValue={20}
                                                    name={key}
                                                    disabled={!edit}
                                                    classContainer="my-2 w-full col-span-2"
                                                    value={
                                                        edit
                                                            ? formState[key]
                                                            : initialValue[key]
                                                    }
                                                    suffixValue="m"
                                                    suffixStyles={{
                                                        width: "3rem",
                                                    }}
                                                    onChange={(e) =>
                                                        handleChange(e.target)
                                                    }
                                                />
                                            </>
                                        ) : (
                                            <label
                                                className={`w-full input input-bordered flex items-center sm:col-span-2 xs:col-span-2 
                                                    col-span-${doubleRow.includes(key) ? "2" : "1"} ${errorBadge?.includes(key) ? "input-error" : ""}`}
                                                title={
                                                    errorBadge?.includes(key)
                                                        ? msg?.errors?.errors.find(
                                                              (e) =>
                                                                  e.attr ===
                                                                  key,
                                                          )?.detail
                                                        : translateKey(
                                                              key,
                                                              true,
                                                          )
                                                }
                                            >
                                                <span className="font-bold p-2 w-fit">
                                                    {translateKey(key, false)}
                                                </span>
                                                <input
                                                    name={key}
                                                    className="grow truncate min-w-[0]"
                                                    readOnly={!edit}
                                                    autoComplete={
                                                        key === "password"
                                                            ? "new-password"
                                                            : "off"
                                                    }
                                                    onChange={(e) => {
                                                        handleChange(e.target);
                                                    }}
                                                    value={formState[key] ?? ""}
                                                    type={
                                                        key === "password" &&
                                                        !seePwd
                                                            ? "password"
                                                            : "text"
                                                    }
                                                    placeholder={
                                                        key === "password"
                                                            ? "********"
                                                            : ""
                                                    }
                                                />

                                                {key === "password" && edit ? (
                                                    !seePwd ? (
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
                                                {edit &&
                                                    (errorBadge &&
                                                    errorBadge.includes(key) ? (
                                                        <span className="badge badge-error right-0">
                                                            {translateKey(
                                                                msg?.errors?.errors.find(
                                                                    (e) =>
                                                                        e.attr ===
                                                                        key,
                                                                )?.code ?? "",
                                                                true,
                                                            )}
                                                        </span>
                                                    ) : edit &&
                                                      optionalFields.includes(
                                                          key,
                                                      ) ? (
                                                        <span className="badge badge-secondary">
                                                            Optional
                                                        </span>
                                                    ) : null)}
                                            </label>
                                        )}
                                    </Fragment>
                                );
                        })}
                    </div>
                    <div className="flex flex-col w-full mt-2 space-y-2">
                        <Alert msg={msg} />

                        <div className="flex justify-center">
                            {edit && (
                                <button
                                    className="w-36 btn btn-success rounded"
                                    disabled={
                                        loading ||
                                        apiOkStatuses.includes(msg?.status ?? 0)
                                    }
                                >
                                    {loading && (
                                        <span className="loading loading-spinner loading-md"></span>
                                    )}
                                    UPDATE
                                </button>
                            )}
                        </div>
                    </div>
                </form>
            </div>
        </div>
    );
};

export default UserSettingsForm;

import { Fragment, useEffect, useState } from "react";

import {
    Menu,
    MenuButton,
    MenuContent,
    Alert,
    ImageUploadCircle,
} from "@componentsReact";

import { useApi, useAuth, useFormReducer } from "@hooks";

import { PencilSquareIcon } from "@heroicons/react/24/outline";

import { getUsersService, patchPeopleService } from "@services";

import { USERS_STATE } from "@utils/reducerFormStates";
import { apiOkStatuses } from "@utils";

import {
    Errors,
    ErrorResponse,
    ExtendedPeople,
    People,
    UsersData,
    UsersServiceData,
} from "@types";

type Props = {
    person: People | null;
    getData: () => void;
};

const PeopleSettingsForm = ({ person, getData }: Props) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const { formState, dispatch } = useFormReducer(USERS_STATE);

    const [users, setUsers] = useState<UsersData[]>([]);
    const [image, setImagen] = useState<string | null>(null);
    const [initialValue, setInitialValue] = useState<People | null>(null);

    const [edit, setEdit] = useState<boolean>(false);
    const [hasImage, setHasImage] = useState(false);
    const [saved, setSaved] = useState<boolean>(false);
    const [loading, setLoading] = useState<boolean>(false);
    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const errorBadge = msg?.errors?.errors?.map((e) => e.attr);
    const optionalFields = [
        "photo_actual_file",
        "last_name",
        "first_name",
        "email",
        "phone",
        "address",
        "institution",
        "position",
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

    const [matchingUsers, setMatchingUsers] = useState<UsersData[] | undefined>(
        undefined,
    );
    const [showMenu, setShowMenu] = useState<
        { type: string; show: boolean } | undefined
    >(undefined);

    const getUsers = async () => {
        try {
            const res = await getUsersService<UsersServiceData>(api);
            setUsers(res.data);
        } catch (err) {
            console.error(err);
        }
    };

    const getPeopleData = () => {
        const extendedUser = {
            id: person?.id ?? 0,
            photo_actual_file: person?.photo_actual_file ?? "",
            user_name: person?.user_name ?? "",
            last_name: person?.last_name ?? "",
            first_name: person?.first_name ?? "",
            email: person?.email ?? "",
            phone: person?.phone ?? "",
            address: person?.address ?? "",
            institution: person?.institution ?? "",
            position: person?.position ?? "",
            user: person?.user ?? "",
        };

        setInitialValue(extendedUser);
        dispatch({
            type: "set",
            payload: extendedUser,
        });
    };

    const patchPerson = async () => {
        try {
            setLoading(true);

            const { id, photo_actual_file, ...data } = formState; // eslint-disable-line

            const formData = new FormData();

            Object.keys(data).forEach((key) => {
                formData.append(key, data[key as keyof typeof data]);
            });

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
                formData.append("photo_actual_file", photo_actual_file);
            }

            if (formState.user_name === "") {
                formData.append("user", "");
            }

            const res = await patchPeopleService<
                ExtendedPeople | ErrorResponse
            >(api, Number(person?.id), formData);
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
                setSaved(true);

                if (formState.user_name === "") {
                    getData();
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setEdit(false);
            setLoading(false);
        }
    };

    const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        patchPerson();
    };

    const handleChange = (
        e:
            | HTMLInputElement
            | HTMLSelectElement
            | { name: string; value: string },
    ) => {
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

        if (name === "user_name") {
            const match = users?.filter((u) =>
                u.username.toLowerCase().includes(value.toLowerCase()),
            );
            setMatchingUsers(match);
            setShowMenu({
                type: name,
                show: true,
            });
        }

        dispatch({
            type: "change_value",
            payload: {
                inputName: name,
                inputValue: alterValue(),
            },
        });
    };

    useEffect(() => {
        if (person) {
            getUsers();
        }
        getPeopleData();
    }, [person]);

    useEffect(() => {
        if (!image) return;
        dispatch({
            type: "change_value",
            payload: {
                inputName: "photo_actual_file",
                inputValue: image,
            },
        });
    }, [image]);

    return person !== null ? (
        <div className="self-center w-full h-full flex flex-col justify-center">
            <div className="bg-base-200 px-6  h-full  rounded-md align-middle">
                <header className="flex justify-between">
                    <h2 className="text-2xl font-bold my-3">
                        Associated person
                    </h2>
                    <button
                        className="flex btn btn-ghost btn-circle self-center"
                        onClick={() => {
                            if (edit && initialValue && !saved) {
                                dispatch({
                                    type: "set",
                                    payload: initialValue,
                                });
                                setImagen(null);
                            }
                            setShowMenu(undefined);
                            setEdit(!edit);
                            setMsg(undefined);
                            setHasImage(false);
                        }}
                    >
                        <PencilSquareIcon title="edit" className="size-8" />
                    </button>
                </header>
                <form
                    className=" flex flex-col items-center justify-center w-full mb-3"
                    onSubmit={handleSubmit}
                >
                    <ImageUploadCircle
                        edit={edit}
                        hasImage={hasImage}
                        image={
                            !edit
                                ? saved
                                    ? formState.photo_actual_file || undefined
                                    : initialValue?.photo_actual_file ||
                                      undefined
                                : image ||
                                  formState.photo_actual_file ||
                                  undefined
                        }
                        setImage={setImagen}
                        setHasImage={setHasImage}
                    />
                    <div className="w-full grid grid-cols-2 gap-2">
                        {Object.keys(formState).map((key) => {
                            const notShow = ["id", "photo_actual_file", "user"];

                            if (!notShow.includes(key))
                                return (
                                    <Fragment key={key}>
                                        <label
                                            className={`w-full input input-bordered flex items-center col-span-2  ${errorBadge?.includes(key) ? "input-error" : ""}`}
                                            title={
                                                errorBadge?.includes(key)
                                                    ? msg?.errors?.errors.find(
                                                          (e) => e.attr === key,
                                                      )?.detail
                                                    : translateKey(key, true)
                                            }
                                        >
                                            <span className="font-bold p-2 w-fit">
                                                {key === "user_name"
                                                    ? "USER"
                                                    : translateKey(key, false)}
                                            </span>

                                            <input
                                                name={key}
                                                className="grow truncate min-w-[0]"
                                                readOnly={!edit}
                                                autoComplete={"off"}
                                                onChange={(e) => {
                                                    handleChange(e.target);
                                                }}
                                                value={formState[key] ?? ""}
                                                type={"text"}
                                                onClick={(e) => {
                                                    if (
                                                        edit &&
                                                        key === "user_name"
                                                    ) {
                                                        handleChange(
                                                            e.target as HTMLInputElement,
                                                        );
                                                        setShowMenu({
                                                            type: key,
                                                            show: true,
                                                        });
                                                    } else {
                                                        setShowMenu({
                                                            type: key,
                                                            show: false,
                                                        });
                                                    }
                                                }}
                                            />
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

                                            {key === "user_name" && edit && (
                                                <MenuButton
                                                    setShowMenu={setShowMenu}
                                                    onMenuClick={() => {
                                                        handleChange({
                                                            name: key,
                                                            value: formState[
                                                                key
                                                            ],
                                                        });
                                                    }}
                                                    showMenu={showMenu}
                                                    typeKey={key}
                                                />
                                            )}
                                        </label>

                                        {showMenu?.show &&
                                        showMenu.type === key &&
                                        key === "user_name" ? (
                                            <div className="col-span-2">
                                                <Menu>
                                                    {(matchingUsers &&
                                                    matchingUsers.length > 0
                                                        ? matchingUsers
                                                        : users
                                                    )?.map((u) => (
                                                        <MenuContent
                                                            key={u.id}
                                                            typeKey={"user"}
                                                            value={u.username}
                                                            alterValue={u.id?.toString()}
                                                            alterFunction={() => {
                                                                dispatch({
                                                                    type: "change_value",
                                                                    payload: {
                                                                        inputName:
                                                                            "user",
                                                                        inputValue:
                                                                            u.id ??
                                                                            0,
                                                                    },
                                                                });
                                                                dispatch({
                                                                    type: "change_value",
                                                                    payload: {
                                                                        inputName:
                                                                            "user_name",
                                                                        inputValue:
                                                                            u.username ??
                                                                            "",
                                                                    },
                                                                });
                                                                setShowMenu(
                                                                    undefined,
                                                                );
                                                            }}
                                                            setShowMenu={
                                                                setShowMenu
                                                            }
                                                        />
                                                    ))}
                                                </Menu>
                                            </div>
                                        ) : null}
                                    </Fragment>
                                );
                        })}
                    </div>
                    <div className="flex flex-col w-full mt-10 space-y-2">
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
    ) : (
        <div className="h-full w-full content-center text-center text-neutral text-2xl font-bold rounded-md bg-neutral-content p-6">
            <span>This user does not have an assigned person</span>
        </div>
    );
};

export default PeopleSettingsForm;

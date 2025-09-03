/* eslint-disable @typescript-eslint/no-unused-vars */
import { Alert, Menu, MenuButton, MenuContent, Modal } from "@componentsReact";

import { useApi, useAuth, useFormReducer } from "@hooks";
import { patchStationVisitService } from "@services";

import { ErrorResponse, Errors, People as PeopleType } from "@types";
import { apiOkStatuses } from "@utils/index";
import { useEffect, useState } from "react";

interface Props {
    people: PeopleType[];
    visit: any;
    reFetch: () => void;
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
}

const AddPeopleModal = ({ people, visit, reFetch, setStateModal }: Props) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const visitId = visit.id;

    const [loading, setLoading] = useState<boolean>(false);

    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [matchingPeople, setMatchingPeople] = useState<
        PeopleType[] | undefined
    >(undefined);

    const [showMenu, setShowMenu] = useState<
        { type: string; show: boolean } | undefined
    >(undefined);

    const { formState, dispatch } = useFormReducer(visit);

    const addPeople = async () => {
        try {
            if (!visitId) return null;
            setLoading(true);

            const {
                name,
                people_id,
                comments,
                navigation_actual_file,
                log_sheet_actual_file,
                log_sheet_filename,
                navigation_filename,
                ...rest
            } = formState;

            const peopleToAdd = people?.find((p) => String(p.id) === people_id);

            if (
                peopleToAdd &&
                peopleToAdd.id &&
                peopleToAdd.first_name &&
                peopleToAdd.last_name
            ) {
                rest.people.push({
                    id: peopleToAdd.id,
                    name: peopleToAdd.first_name + " " + peopleToAdd.last_name,
                });
            } else {
                setMsg({
                    status: 400,
                    msg: "People not found",
                    errors: {
                        type: "People not found",
                        errors: [
                            {
                                attr: "name",
                                code: "not_found",
                                detail: "People not found, please select a valid people",
                            },
                        ],
                    },
                });
                setLoading(false);
                return;
            }

            rest.campaign = rest.campaign ?? "";

            const formData = new FormData();

            if (rest.people.length !== 0) {
                Object.entries(rest).forEach(([key, value]) => {
                    if (key === "people" && Array.isArray(value)) {
                        value.forEach((p: { id: number; name: string }) => {
                            formData.append("people", String(p.id));
                        });
                    } else {
                        formData.append(key, value as unknown as string);
                    }
                });
            }

            const res = await patchStationVisitService<ErrorResponse>(
                api,
                visitId,
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
                    status: 200,
                    msg: "People Visit updated successfully",
                });
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (formState.people_id) {
            const peopleToAdd = people.find(
                (p) => String(p.id) === formState.people_id,
            );
            dispatch({
                type: "change_value",
                payload: {
                    inputName: "name",
                    inputValue:
                        peopleToAdd?.first_name + " " + peopleToAdd?.last_name,
                },
            });
        }
    }, [formState.people_id]);

    const handleCloseModal = () => {
        reFetch();
    };

    const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        addPeople();
    };

    const errorBadge = msg?.errors?.errors?.map((e) => e.attr);

    return (
        <Modal
            close={false}
            modalId={"AddVisitPeople"}
            size={"sm"}
            handleCloseModal={() => handleCloseModal()}
            setModalState={setStateModal}
        >
            <div className="w-full flex grow mb-2">
                <h3 className="font-bold text-center text-2xl my-2 w-full self-center">
                    Add
                </h3>
            </div>
            <form className="form-control space-y-4" onSubmit={handleSubmit}>
                <div className="form-control space-y-2">
                    <>
                        <label
                            className={`w-full input input-bordered flex items-center gap-2 ${errorBadge?.includes("people") ? "input-error" : ""}`}
                            title={
                                errorBadge?.includes("people")
                                    ? msg?.errors?.errors.find(
                                          (e) => e.attr === "people",
                                      )?.detail
                                    : "People"
                            }
                        >
                            <div className="label text-2xl">
                                <span className="font-bold">People</span>
                            </div>
                            <input
                                type="text"
                                value={formState["name"] ?? ""}
                                onChange={(e) => {
                                    const value = e.target.value;
                                    dispatch({
                                        type: "change_value",
                                        payload: {
                                            inputName: "name",
                                            inputValue: value,
                                        },
                                    });
                                    const parts = value
                                        .toLowerCase()
                                        .split(" ");
                                    const match = people?.filter((p) =>
                                        parts.every(
                                            (part) =>
                                                p.first_name
                                                    .toLowerCase()
                                                    .includes(part) ||
                                                p.last_name
                                                    .toLowerCase()
                                                    .includes(part),
                                        ),
                                    );
                                    setMatchingPeople(match);
                                    setShowMenu({ show: true, type: "name" });
                                }}
                                onClick={() => {
                                    setShowMenu({ show: true, type: "name" });
                                }}
                                className="grow"
                                autoComplete="off"
                            />
                            {errorBadge && (
                                <span className="badge badge-error absolute right-0 mb-12 mr-2">
                                    {errorBadge.includes("name")
                                        ? msg?.errors?.errors.find(
                                              (e) => e.attr === "name",
                                          )?.code
                                        : ""}
                                </span>
                            )}
                            <MenuButton
                                setShowMenu={setShowMenu}
                                showMenu={showMenu}
                                typeKey={"name"}
                            />
                        </label>
                        {showMenu?.show && showMenu.type === "name" ? (
                            <Menu>
                                {(matchingPeople && matchingPeople.length > 0
                                    ? matchingPeople
                                    : people
                                )?.map((p) => (
                                    <MenuContent
                                        key={p.id}
                                        disabled={
                                            p.id ===
                                            formState.people.find(
                                                (fp: { id: number }) =>
                                                    fp.id === p.id,
                                            )?.id
                                        }
                                        typeKey={"people_id"}
                                        value={p.first_name + " " + p.last_name}
                                        alterValue={String(p.id)}
                                        dispatch={dispatch}
                                        setShowMenu={setShowMenu}
                                    />
                                ))}
                            </Menu>
                        ) : null}
                    </>
                </div>
                <Alert msg={msg} />
                {loading && (
                    <div className="w-full text-center">
                        <span className="loading loading-spinner loading-lg self-center"></span>
                    </div>
                )}
                <button
                    className="btn btn-success self-center w-3/12"
                    disabled={
                        loading || apiOkStatuses.includes(Number(msg?.status))
                    }
                >
                    {" "}
                    Save{" "}
                </button>
            </form>
        </Modal>
    );
};

export default AddPeopleModal;

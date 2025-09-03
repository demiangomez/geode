// ------------------------------------------React------------------------------------------
import { useEffect, useState } from "react";

// ------------------------------------------Componentes de React------------------------------------------
import {
    Alert,
    ConfirmDeleteModal,
    Menu,
    MenuButton,
    MenuContent,
    Modal,
} from "@componentsReact";

// ------------------------------------------Hooks------------------------------------------
import { useApi, useAuth, useFormReducer } from "@hooks";

// ------------------------------------------Services------------------------------------------
import {
    delStationCampaignService,
    patchStationCampaignService,
    postStationCampaignService,
} from "@services";

import { apiOkStatuses, showModal } from "@utils";
import { CAMPAIGN_STATE } from "@utils/reducerFormStates";
import { XMarkIcon } from "@heroicons/react/24/outline";

// ------------------------------------------Interfaces------------------------------------------
import { CampaignsData, ErrorResponse, Errors, People } from "@types";
interface Props {
    modalType: string;
    campaign: CampaignsData | undefined;
    reFetch: () => void;
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
    people: People[] | undefined;
}

// ##############################################################################################
// ------------------------------------------COMPONENTE------------------------------------------
// ##############################################################################################

const AddCampaignModal = ({
    campaign,
    modalType,
    reFetch,
    setStateModal,
    people,
}: Props) => {
    // ------------------------------------------Constantes------------------------------------------

    const { formState, dispatch } = useFormReducer(CAMPAIGN_STATE);
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    // ------------------------------------------UseStates------------------------------------------

    //Manejo de informacion
    const [peopleSelected, setPeopleSelected] = useState<People[]>([]);
    const [matchingPeople, setMatchingPeople] = useState<People[]>([]);

    //Manejo de componentes
    const [loading, setLoading] = useState<boolean>(false);

    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [showMenu, setShowMenu] = useState<
        { show: boolean; type: string } | undefined
    >({ show: false, type: "" });

    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    // ------------------------------------------Funciones------------------------------------------

    //Añadir persona seleccionada
    const addUserSelect = (targetPerson: People) => {
        setPeopleSelected((prev) => [...prev, targetPerson]);

        dispatch({
            type: "change_value",
            payload: {
                inputName: "default_people",
                inputValue: [
                    ...peopleSelected.map((p) => p.id.toString()),
                    targetPerson.id.toString(),
                ],
            },
        });
    };

    //Eliminar persona seleccionada
    const deleteUserSelect = (id: number) => {
        //Lo elimino del peopleSelected
        setPeopleSelected((prev) => prev.filter((p) => p.id !== id));

        //Lo elimino del formState
        const newIds = formState.default_people.filter(
            (pId) => pId !== String(id),
        );
        dispatch({
            type: "change_value",
            payload: {
                inputName: "default_people",
                inputValue: newIds,
            },
        });
    };

    // Validar si fue seleccionado y ocuparse de agregar o eliminar a la persona
    const addOrDeletePeople = (ppl: People) => {
        const targetPerson = people?.find((p) => p.id === ppl.id);

        //Si no existe la persona
        if (!targetPerson) return;

        //Si fue seleccionado
        const isSelected = peopleSelected.some((p) => p.id === targetPerson.id);
        if (!isSelected) {
            addUserSelect(targetPerson);
        } else {
            deleteUserSelect(targetPerson.id);
        }
    };

    // Filtar personas buscadas
    const handleChange = (value: string) => {
        const parts = value.toLowerCase().split(" ");
        const match = people?.filter((p) =>
            parts.every(
                (part) =>
                    p.first_name.toLowerCase().includes(part) ||
                    p.last_name.toLowerCase().includes(part),
            ),
        );
        if (match) setMatchingPeople(match);
    };

    const addCampaign = async (payload?: any) => {
        try {
            setLoading(true);
            const res = await postStationCampaignService<any>(
                api,
                payload ?? formState,
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
                    msg: "Campaign added successfully",
                });
                reFetch();
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const delCampaign = async () => {
        try {
            setLoading(true);
            const res = await delStationCampaignService<ErrorResponse>(
                api,
                Number(campaign?.id),
            );
            if (res) {
                if ("status" in res && res.status === "success") {
                    setMsg({
                        status: res.statusCode,
                        msg: res.msg,
                    });
                    reFetch();
                } else {
                    setMsg({
                        status: res.statusCode,
                        msg: res.response.type,
                        errors: res.response,
                    });
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const editCampaign = async (payload?: any) => {
        try {
            setLoading(true);

            const res = await patchStationCampaignService<
                CampaignsData | ErrorResponse
            >(api, Number(campaign?.id), payload ?? formState);
            if ("status" in res) {
                setMsg({
                    status: res.statusCode,
                    msg: res.response.type,
                    errors: res.response,
                });
            } else {
                setMsg({
                    status: res.statusCode,
                    msg: "Campaign edited successfully",
                });
                reFetch();
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleCloseModal = () => {
        dispatch({ type: "clear" });
    };

    const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();

        if (modalType === "edit") {
            await editCampaign();
        } else if (modalType === "add") {
            await addCampaign();
        }
    };

    const errorBadge = msg?.errors?.errors?.map((e) => e.attr);

    // ------------------------------------------useEffects------------------------------------------

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    useEffect(() => {
        //setear la campaña
        if (campaign) {
            dispatch({
                type: "set",
                payload: modalType === "edit" ? campaign : CAMPAIGN_STATE,
            });

            //Si hay personas, cargarlas en el selected
            if (campaign.default_people && people && modalType === "edit") {
                // Convertir IDs a objetos People completos
                const selectedPeople = campaign.default_people
                    .map((id: number) =>
                        people.find((person) => person.id === id),
                    )
                    .filter((person): person is People => person !== undefined);

                dispatch({
                    type: "change_value",
                    payload: {
                        inputName: "default_people",
                        inputValue: campaign.default_people.map((id) =>
                            String(id),
                        ),
                    },
                });
                setPeopleSelected(selectedPeople);
            }
        }
    }, [campaign, people]);

    // Limpiar filtro cuando se cierre el menú
    useEffect(() => {
        if (!showMenu?.show) {
            setMatchingPeople([]);
        }
    }, [showMenu]);

    return (
        <Modal
            close={false}
            modalId={"EditCampaigns"}
            size={"sm"}
            handleCloseModal={() => handleCloseModal()}
            setModalState={setStateModal}
        >
            <div className="w-full flex grow mb-2">
                <h3 className="font-bold text-center text-2xl my-2 w-full self-center">
                    {modalType?.charAt(0).toUpperCase() + modalType?.slice(1)}
                </h3>
            </div>
            <form className="space-y-4" onSubmit={handleSubmit}>
                <div className="grid grid-cols-1 gap-4">
                    {Object.keys(formState).map((key) => {
                        const disabled = key === "id";
                        const inputsToDatePicker = ["start_date", "end_date"];
                        if (key === "default_people") {
                            return (
                                <div key={key} className="">
                                    <div className="flex flex-col space-y-1">
                                        <label
                                            className={
                                                "w-full input input-bordered flex items-center text-nowrap"
                                            }
                                            title={
                                                errorBadge?.includes(key)
                                                    ? msg?.errors?.errors.find(
                                                          (e) => e.attr === key,
                                                      )?.detail
                                                    : peopleSelected
                                                          .map(
                                                              (p) =>
                                                                  `${p.first_name} ${p.last_name}`,
                                                          )
                                                          .join(", ")
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
                                                name={key}
                                                readOnly={false}
                                                autoComplete="off"
                                                type="text"
                                                className="grow"
                                                onChange={(e) => {
                                                    handleChange(
                                                        e.target.value,
                                                    );
                                                }}
                                                onClick={(e) => {
                                                    handleChange(
                                                        (
                                                            e.target as HTMLInputElement
                                                        ).value,
                                                    );
                                                    setShowMenu({
                                                        type: key,
                                                        show: true,
                                                    });
                                                }}
                                            />
                                            <MenuButton
                                                setShowMenu={setShowMenu}
                                                showMenu={showMenu}
                                                typeKey={key}
                                            />
                                        </label>
                                    </div>

                                    {/* Badges de personas seleccionadas, igual que en AddVisitModal */}
                                    {peopleSelected.length > 0 && (
                                        <div className="grid grid-cols-4 gap-2 w-full my-4">
                                            {peopleSelected.map((p) => {
                                                return (
                                                    <div
                                                        key={p.id}
                                                        className="badge badge-secondary px-2 py-4 flex items-center min-w-0 max-w-full"
                                                        title={`${p.first_name} ${p.last_name}`}
                                                    >
                                                        <span className="overflow-hidden text-ellipsis whitespace-nowrap max-w-3/4">
                                                            {`${p.first_name} ${p.last_name}`}
                                                        </span>
                                                        <XMarkIcon
                                                            className="ml-2 w-5 h-5 min-w-5 min-h-5 cursor-pointer hover:bg-[#4c566a] rounded"
                                                            onClick={() => {
                                                                deleteUserSelect(
                                                                    p.id,
                                                                );
                                                            }}
                                                        />
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    )}

                                    {/* Menú de selección con búsqueda, igual que en AddVisitModal */}
                                    {showMenu?.show &&
                                        showMenu?.type === key && (
                                            <Menu>
                                                {(matchingPeople.length > 0
                                                    ? matchingPeople
                                                    : people
                                                )?.map((ppl: People) => {
                                                    const displayName = `${ppl.first_name} ${ppl.last_name}`;

                                                    return (
                                                        <MenuContent
                                                            multiple={true}
                                                            multipleSelected={peopleSelected.map(
                                                                (p) =>
                                                                    String(
                                                                        p.id,
                                                                    ),
                                                            )}
                                                            dispatch={dispatch}
                                                            typeKey={key}
                                                            value={displayName}
                                                            uniqueId={String(
                                                                ppl.id,
                                                            )}
                                                            alterValue={ppl}
                                                            alterFunctionWithValue={
                                                                addOrDeletePeople
                                                            }
                                                            setShowMenu={
                                                                setShowMenu
                                                            }
                                                        />
                                                    );
                                                })}
                                            </Menu>
                                        )}
                                </div>
                            );
                        } else {
                            return (
                                <div key={key} className="flex w-full">
                                    <label
                                        key={key}
                                        className={`w-full input input-bordered flex items-center gap-2 ${
                                            errorBadge?.includes(key)
                                                ? "input-error"
                                                : ""
                                        } `}
                                        title={
                                            errorBadge?.includes(key)
                                                ? msg?.errors?.errors.find(
                                                      (e) => e.attr === key,
                                                  )?.detail
                                                : Array.isArray(
                                                        formState[
                                                            key as keyof typeof formState
                                                        ],
                                                    )
                                                  ? (
                                                        formState[
                                                            key as keyof typeof formState
                                                        ] as string[]
                                                    ).join(", ")
                                                  : (
                                                        formState[
                                                            key as keyof typeof formState
                                                        ] ?? ""
                                                    ).toString()
                                        }
                                    >
                                        <div className="label text-nowrap">
                                            <span className="font-bold">
                                                {key
                                                    .toUpperCase()
                                                    .replace("_", " ")
                                                    .replace("_", " ")}
                                            </span>
                                        </div>
                                        <input
                                            type={
                                                inputsToDatePicker.includes(key)
                                                    ? "date"
                                                    : "text"
                                            }
                                            value={
                                                formState[
                                                    key as keyof typeof formState
                                                ] ?? ""
                                            }
                                            onChange={(e) => {
                                                const value = e.target.value;
                                                dispatch({
                                                    type: "change_value",
                                                    payload: {
                                                        inputName: key,
                                                        inputValue: value,
                                                    },
                                                });
                                            }}
                                            onClick={() => {
                                                setShowMenu({
                                                    type: "name",
                                                    show: false,
                                                });
                                            }}
                                            className="w-full"
                                            autoComplete="off"
                                            placeholder={
                                                inputsToDatePicker.includes(key)
                                                    ? "YYYY-MM-DD"
                                                    : ""
                                            }
                                            disabled={disabled}
                                        />
                                        {errorBadge &&
                                            errorBadge.includes(key) && (
                                                <span className="badge badge-error absolute right-0 mb-12 mr-2">
                                                    {errorBadge.includes(key)
                                                        ? msg?.errors?.errors.find(
                                                              (e) =>
                                                                  e.attr ===
                                                                  key,
                                                          )?.code
                                                        : ""}
                                                </span>
                                            )}
                                    </label>
                                </div>
                            );
                        }
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
                            type="button"
                            className="btn btn-error w-3/12"
                            disabled={apiOkStatuses.includes(
                                Number(msg?.status),
                            )}
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
                        className="btn btn-success self-center w-3/12"
                        disabled={
                            loading ||
                            apiOkStatuses.includes(Number(msg?.status))
                        }
                    >
                        {" "}
                        Save{" "}
                    </button>
                    {modals && modals?.title === "ConfirmDelete" && (
                        <ConfirmDeleteModal
                            msg={msg}
                            loading={loading}
                            confirmRemove={() => delCampaign()}
                            closeModal={() => {
                                setModals({
                                    show: false,
                                    title: "",
                                    type: "edit",
                                });
                            }}
                        />
                    )}
                </div>
            </form>
        </Modal>
    );
};

export default AddCampaignModal;

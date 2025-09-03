import { useEffect, useRef, useState } from "react";
import {
    AddFileModal,
    Alert,
    Menu,
    MenuButton,
    MenuContent,
    Modal,
    Spinner,
    StationPeopleModal,
} from "@componentsReact";

import { useApi, useAuth, useFormReducer } from "@hooks";
import {
    ErrorResponse,
    Errors,
    PeopleServiceData,
    People as PeopleType,
    StationCampaignsData,
    StationData,
    StationPostVisitData,
    StationVisitsFilesData,
    StationVisitsFilesServiceData,
} from "@types";

import { apiOkStatuses, classHtml, showModal } from "@utils";

import { VISIT_STATE } from "@utils/reducerFormStates";
import {
    getPeopleService,
    getStationVisitFilesService,
    getStationVisitGnssFilesService,
    getStationVisitsImagesService,
    postStationVisitService,
} from "@services";

import {
    PlusCircleIcon,
    UserPlusIcon,
    XMarkIcon,
} from "@heroicons/react/24/outline";
import QuillText from "@components/map/QuillText";

interface Props {
    station: StationData;
    campaignB: StationCampaignsData | undefined;
    campaigns: StationCampaignsData[] | undefined;
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
    reFetch: () => void;
    closeModal: () => void;
}

const AddVisitModal = ({
    station,
    campaignB,
    campaigns,
    setStateModal,
    reFetch,
    closeModal,
}: Props) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const [loading, setLoading] = useState<boolean>(false);

    const [loadingGnss, setLoadingGnss] = useState<boolean>(false);

    const [loadingFiles, setLoadingFiles] = useState<boolean>(false);

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

    const [fileType, setFileType] = useState<string | undefined>(undefined);

    const [visitId, setVisitId] = useState<number | undefined>(undefined);

    const [step, setStep] = useState<number>(1);

    const [matchingCampaigns, setMatchingCampaigns] = useState<
        StationCampaignsData[] | undefined
    >(undefined);

    const [people, setPeople] = useState<PeopleType[]>([]);

    const [peopleSelected, setPeopleSelected] = useState<PeopleType[]>([]);

    const [matchingPeople, setMatchingPeople] = useState<
        PeopleType[] | undefined
    >(undefined);

    // FILES

    const [images, setImages] = useState<StationVisitsFilesData[] | undefined>(
        undefined,
    );

    const [files, setFiles] = useState<StationVisitsFilesData[] | undefined>(
        undefined,
    );

    const [gnssFiles, setGnssFiles] = useState<
        StationVisitsFilesData[] | undefined
    >(undefined);

    const { formState, dispatch } = useFormReducer(VISIT_STATE);

    const deleteUserSelectById = (id: number) => {
        const result = peopleSelected.filter((p) => p.id !== id);
        setPeopleSelected(result);
    };

    const inputRefCampaign = useRef<HTMLInputElement>(null);

    const inputRefPeople = useRef<HTMLInputElement>(null);

    const selectRef = (key: string) => {
        return key === "campaign"
            ? inputRefCampaign
            : key === "people"
              ? inputRefPeople
              : null;
    };

    const formattedState = {
        ...VISIT_STATE,
        campaign: campaignB
            ? "(" +
              campaignB?.name +
              ")" +
              " " +
              campaignB?.start_date +
              " - " +
              campaignB?.end_date
            : "",
        station: String(station?.api_id),
    };

    const getPeople = async () => {
        try {
            const res = await getPeopleService<PeopleServiceData>(api);
            setPeople(res.data);
        } catch (err) {
            console.error(err);
        }
    };

    const addVisit = async () => {
        try {
            setLoading(true);

            const formData = new FormData();

            Object.entries(formState).forEach(([key, value]) => {
                if (
                    key === "campaign" ||
                    key === "people" ||
                    key === "comments"
                ) {
                    if (key === "campaign") {
                        if (!value) return formData.append(key, "");
                        const splittedValue = value
                            .split("(")
                            .pop()
                            ?.split(")")[0];
                        const campaignToAdd = campaigns?.find(
                            (c) => c.name === splittedValue,
                        );
                        formData.append(key, String(campaignToAdd?.id));
                    }
                    if (key === "people") {
                        if (peopleSelected.length === 0) return;

                        peopleSelected.forEach((person) => {
                            formData.append(key, String(person.id));
                        });
                    }
                    if (key === "comments") {
                        formData.append("comments", classHtml(value));
                    }
                } else {
                    formData.append(key, value);
                }
            });

            const res = await postStationVisitService<
                StationPostVisitData | ErrorResponse
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
                        msg: "Visit added successfully",
                    });
                    setVisitId(res.id);
                    setStep(2);
                    setTimeout(() => {
                        setMsg(undefined);
                    }, 3500);
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const getVisitImagesById = async () => {
        try {
            if (!visitId) return null;
            setLoading(true);
            const res =
                await getStationVisitsImagesService<StationVisitsFilesServiceData>(
                    api,
                    {
                        limit: 0,
                        offset: 0,
                        visit_api_id: String(visitId),
                        thumbnail: true,
                    },
                );
            if (res.statusCode === 200) {
                setImages(res.data);
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    const getVisitsAttachedFiles = async () => {
        try {
            if (!visitId) return null;
            setLoadingFiles(true);
            const res =
                await getStationVisitFilesService<StationVisitsFilesServiceData>(
                    api,
                    {
                        limit: 0,
                        offset: 0,
                        visit_api_id: String(visitId),
                        only_metadata: true,
                    },
                );

            if (res.statusCode === 200) {
                setFiles(res.data);
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoadingFiles(false);
        }
    };

    const getVisitsGnssFiles = async () => {
        try {
            if (!visitId) return null;
            setLoadingGnss(true);
            const res =
                await getStationVisitGnssFilesService<StationVisitsFilesServiceData>(
                    api,
                    {
                        limit: 0,
                        offset: 0,
                        visit_api_id: String(visitId),
                        only_metadata: true,
                    },
                );
            if (res.statusCode === 200) {
                setGnssFiles(res.data);
            }
        } catch (error) {
            console.error(error);
        } finally {
            setLoadingGnss(false);
        }
    };

    const getFiles = () => {
        getVisitsAttachedFiles();
        getVisitsGnssFiles();
    };

    const handleChange = (
        e: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement,
    ) => {
        const { name, value } = e;

        dispatch({
            type: "change_value",
            payload: {
                inputName: name,
                inputValue: value,
            },
        });

        if (name === "people") {
            const parts = value.toLowerCase().split(" ");
            const match = people?.filter((p) =>
                parts.every(
                    (part) =>
                        p.first_name.toLowerCase().includes(part) ||
                        p.last_name.toLowerCase().includes(part),
                ),
            );

            setMatchingPeople(match);
        }

        if (name === "campaign") {
            const match = campaigns?.filter(
                (c) =>
                    c.name
                        .toLocaleLowerCase()
                        .includes(value.toLocaleLowerCase().trim()) ||
                    c.start_date.includes(value) ||
                    c.end_date.includes(value),
            );

            setMatchingCampaigns(match);
        }
    };

    const handleChangePhoto = async (e: HTMLInputElement) => {
        const { files, name } = e;

        if (!files || files.length === 0) return;

        const file = files[0];
        dispatch({
            type: "change_value",
            payload: {
                inputName: name,
                inputValue: file,
            },
        });
    };

    useEffect(() => {
        getPeople();
    }, []);

    const handleCloseModal = () => {
        reFetch();
    };

    const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        addVisit();
    };

    useEffect(() => {
        dispatch({
            type: "set",
            payload: formattedState,
        });
    }, [station, campaignB]);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    useEffect(() => {
        if (showMenu) {
            const ref = selectRef(showMenu.type);
            if (ref && ref.current) {
                ref.current.focus();
            }
        }
    }, [showMenu]);

    useEffect(() => {
        if (formState.campaign) {
            const campaignSelected = campaigns?.find(
                (campaign) =>
                    formState.campaign ===
                    "(" +
                        campaign.name +
                        ")" +
                        " " +
                        campaign.start_date +
                        " - " +
                        campaign.end_date,
            );

            if (campaignSelected && campaignSelected.default_people) {
                const peopleIds = campaignSelected.default_people;
                const selectedPeople = peopleIds
                    .map((id) => {
                        return people.find((person) => person.id === id);
                    })
                    .filter(
                        (person): person is PeopleType => person !== undefined,
                    );

                setPeopleSelected(selectedPeople);
            }
        }
    }, [formState, campaigns, people]);

    return (
        <Modal
            close={true}
            modalId={"AddVisit"}
            size={"md"}
            handleCloseModal={() => handleCloseModal()}
            setModalState={setStateModal}
        >
            <div className="w-full flex grow mb-2">
                <h3 className="font-bold text-center text-2xl my-2 w-full self-center">
                    Add
                </h3>
            </div>

            <ul className="steps w-full mb-6">
                <li
                    onClick={() => setStep(1)}
                    className={`step ${step && "step-neutral"} cursor-pointer`}
                >
                    Register Visit
                </li>
                <li
                    onClick={() => visitId && setStep(2)}
                    className={`step ${(step === 2 || step === 3) && "step-neutral"} cursor-pointer`}
                >
                    Visit Images
                </li>
                <li
                    onClick={() => visitId && setStep(3)}
                    className={`step ${step === 3 && "step-neutral"} cursor-pointer`}
                >
                    Files
                </li>
            </ul>
            <form className="form-control space-y-4" onSubmit={handleSubmit}>
                <div className="form-control space-y-2">
                    {step === 1 ? (
                        Object.entries(formState || {}).map(([key], index) => {
                            const inputsToDisable = ["station"];
                            const inputsToDatePicker = ["date"];
                            const inputsToFile = [
                                "log_sheet_file",
                                "navigation_file",
                            ];

                            const optionalFields = [
                                "log_sheet_file",
                                "navigation_file",
                            ];
                            const errorBadge = msg?.errors?.errors?.find(
                                (error) => error.attr === key,
                            );

                            return (
                                <div className="flex flex-col" key={index}>
                                    {errorBadge && (
                                        <div className="badge badge-error gap-2 self-end -mb-2 z-[1]">
                                            {errorBadge.code.toUpperCase()}
                                        </div>
                                    )}
                                    {inputsToFile.includes(key) ? (
                                        <div className="flex flex-col">
                                            <label
                                                htmlFor={key}
                                                className=" font-bold"
                                            >
                                                {key
                                                    .toUpperCase()
                                                    .replace("_", " ")
                                                    .replace("_", " ")}
                                            </label>
                                            <div className="flex gap-2 items-center w-full">
                                                <input
                                                    type="file"
                                                    name={key}
                                                    className={` ${errorBadge && errorBadge?.attr === "image" ? "file-input-error" : ""} file-input file-input-bordered w-full `}
                                                    onChange={(e) => {
                                                        handleChangePhoto(
                                                            e.target,
                                                        );
                                                    }}
                                                    disabled={
                                                        visitId ? true : false
                                                    }
                                                />

                                                {optionalFields.includes(
                                                    key,
                                                ) && (
                                                    <span className="badge badge-secondary">
                                                        Optional
                                                    </span>
                                                )}
                                            </div>
                                        </div>
                                    ) : key === "comments" ? (
                                        <div className="flex flex-col w-full">
                                            <div className="label">
                                                <span className="font-bold">
                                                    {key
                                                        .toUpperCase()
                                                        .replace("_", " ")
                                                        .replace("_", " ")}
                                                </span>
                                            </div>
                                            <QuillText
                                                clase="h-[120px] max-h-[120px] mb-8"
                                                value={formState.comments ?? ""}
                                                setValue={(value: string) => {
                                                    dispatch({
                                                        type: "change_value",
                                                        payload: {
                                                            inputName:
                                                                "comments",
                                                            inputValue: value,
                                                        },
                                                    });
                                                }}
                                            />
                                        </div>
                                    ) : (
                                        <div className="flex w-full">
                                            <label
                                                id={key}
                                                className={`w-full input input-bordered flex items-center 
                                                    gap-2 ${errorBadge ? "input-error" : ""} 
                                                    ${inputsToDatePicker.includes(key) ? "w-11/12" : ""}`}
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
                                                    type={
                                                        inputsToDatePicker.includes(
                                                            key,
                                                        )
                                                            ? "date"
                                                            : inputsToFile.includes(
                                                                    key,
                                                                )
                                                              ? "file"
                                                              : "text"
                                                    }
                                                    ref={selectRef(key)}
                                                    name={key}
                                                    value={
                                                        key === "station"
                                                            ? station?.api_id
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
                                                    disabled={inputsToDisable.includes(
                                                        key,
                                                    )}
                                                    placeholder={
                                                        inputsToDatePicker.includes(
                                                            key,
                                                        )
                                                            ? "YYYY-MM-DD"
                                                            : ""
                                                    }
                                                />

                                                {(key === "campaign" ||
                                                    key === "people") && (
                                                    <MenuButton
                                                        setShowMenu={
                                                            setShowMenu
                                                        }
                                                        showMenu={showMenu}
                                                        typeKey={key}
                                                    />
                                                )}

                                                {key === "people" && (
                                                    <button
                                                        className="btn btn-ghost btn-circle tooltip tooltip-bottom"
                                                        data-tip="Create People"
                                                        type="button"
                                                        onClick={() => {
                                                            setModals &&
                                                                setModals({
                                                                    show: true,
                                                                    title: "EditPerson",
                                                                    type: "add",
                                                                });
                                                        }}
                                                    >
                                                        <UserPlusIcon
                                                            strokeWidth={1.5}
                                                            stroke="currentColor"
                                                            className="w-8 h-8 justify-self-center"
                                                        />
                                                    </button>
                                                )}
                                            </label>
                                        </div>
                                    )}
                                    {key === "people" &&
                                        peopleSelected.length > 0 && (
                                            <div className="grid grid-cols-7 lg:grid-cols-4 gap-2 w-full my-4">
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
                                                                onClick={() =>
                                                                    deleteUserSelectById(
                                                                        p.id,
                                                                    )
                                                                }
                                                            />
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        )}

                                    {showMenu?.show &&
                                    showMenu.type === key &&
                                    key === "campaign" ? (
                                        <Menu>
                                            {(matchingCampaigns &&
                                            matchingCampaigns.length > 0
                                                ? matchingCampaigns
                                                : campaigns
                                            )?.map((campaign) => (
                                                <MenuContent
                                                    key={
                                                        campaign.id +
                                                        campaign.name
                                                    }
                                                    typeKey={key}
                                                    value={
                                                        "(" +
                                                        campaign.name +
                                                        ")" +
                                                        " " +
                                                        campaign.start_date +
                                                        " - " +
                                                        campaign.end_date
                                                    }
                                                    dispatch={dispatch}
                                                    setShowMenu={setShowMenu}
                                                />
                                            ))}
                                        </Menu>
                                    ) : (
                                        showMenu?.show &&
                                        showMenu.type === key &&
                                        key === "people" && (
                                            <Menu>
                                                {(matchingPeople &&
                                                matchingPeople.length > 0
                                                    ? matchingPeople
                                                    : people
                                                )?.map((ppl) => {
                                                    const displayName = `${ppl.first_name} ${ppl.last_name}`;

                                                    return (
                                                        <MenuContent
                                                            multiple={true}
                                                            multipleSelected={peopleSelected.map(
                                                                (p) => p.id,
                                                            )}
                                                            key={ppl.id}
                                                            typeKey={key}
                                                            value={displayName}
                                                            uniqueId={ppl.id}
                                                            dispatch={dispatch}
                                                            alterFunctionWithValue={() => {
                                                                // Buscar la persona específica por ID usando el key del componente
                                                                const targetPerson =
                                                                    people.find(
                                                                        (p) =>
                                                                            p.id ===
                                                                            ppl.id,
                                                                    );

                                                                if (
                                                                    !targetPerson
                                                                )
                                                                    return;

                                                                const isSelected =
                                                                    peopleSelected.some(
                                                                        (p) =>
                                                                            p.id ===
                                                                            targetPerson.id,
                                                                    );

                                                                if (
                                                                    isSelected
                                                                ) {
                                                                    setPeopleSelected(
                                                                        (
                                                                            prev,
                                                                        ) =>
                                                                            prev.filter(
                                                                                (
                                                                                    p,
                                                                                ) =>
                                                                                    p.id !==
                                                                                    targetPerson.id,
                                                                            ),
                                                                    );
                                                                } else {
                                                                    setPeopleSelected(
                                                                        (
                                                                            prev,
                                                                        ) => [
                                                                            ...prev,
                                                                            targetPerson,
                                                                        ],
                                                                    );
                                                                }
                                                            }}
                                                            setShowMenu={
                                                                setShowMenu
                                                            }
                                                        />
                                                    );
                                                })}
                                            </Menu>
                                        )
                                    )}
                                </div>
                            );
                        })
                    ) : step === 2 ? (
                        <div className="flex flex-col items-center mb-12 bg-neutral-content rounded-md">
                            <h3 className="font-bold inline-flex items-center text-xl my-2">
                                Visit Images
                                <button
                                    className="btn btn-ghost btn-circle ml-2"
                                    type="button"
                                    onClick={() => {
                                        setFileType("visitImage");
                                        setModals({
                                            show: true,
                                            title: "AddFile",
                                            type: "add",
                                        });
                                    }}
                                >
                                    <PlusCircleIcon
                                        strokeWidth={1.5}
                                        stroke="currentColor"
                                        className="w-8 h-10"
                                    />
                                </button>
                            </h3>
                            <div
                                className={`max-h-72 px-2 overflow-y-auto grid grid-cols-${images && images.length > 1 ? "3" : "1"} gap-4 grid-flow-dense`}
                            >
                                {images
                                    ? images?.map((img) => (
                                          <div
                                              key={img.id}
                                              className="flex flex-col items-center break-words pb-2 rounded-md"
                                          >
                                              <img
                                                  src={`data:image/*;base64,${img.actual_image ?? ""}`}
                                                  alt={img.name}
                                                  className="size-60 object-cover rounded-md"
                                              />
                                              <span className="text-md font-medium mt-2 mx-auto w-auto">
                                                  {img.name}
                                              </span>
                                              {img.description && (
                                                  <span className="text-sm mt-2 mx-auto w-full">
                                                      {img.description}
                                                  </span>
                                              )}
                                          </div>
                                      ))
                                    : null}
                            </div>
                        </div>
                    ) : (
                        step === 3 && (
                            <>
                                <div className="flex flex-col items-center rounded-md bg-neutral-content">
                                    <h3 className="font-bold inline-flex border-b-2 w-full justify-center items-center text-xl my-2">
                                        Visit Observation Files
                                        <button
                                            className="btn btn-ghost btn-circle ml-2"
                                            type="button"
                                            onClick={() => {
                                                setFileType("gnss");
                                                setModals({
                                                    show: true,
                                                    title: "AddFile",
                                                    type: "add",
                                                });
                                            }}
                                        >
                                            <PlusCircleIcon
                                                strokeWidth={1.5}
                                                stroke="currentColor"
                                                className="w-8 h-10"
                                            />
                                        </button>
                                    </h3>
                                    <div className="flex flex-col flex-grow w-full max-h-56 overflow-y-auto p-2">
                                        {loadingGnss ? (
                                            <div className="w-full text-center">
                                                <Spinner size="lg" />
                                            </div>
                                        ) : (
                                            <div
                                                className={`grid
                                                    ${gnssFiles && gnssFiles.length > 1 && !loadingGnss ? "grid-cols-2 md:grid-cols-1" : "grid-cols-1"} 
                                                    grid-flow-dense gap-2`}
                                            >
                                                {(!gnssFiles ||
                                                    gnssFiles.length === 0) && (
                                                    <div className="text-center text-neutral text-2xl font-bold w-full rounded-md bg-neutral-content p-4">
                                                        There are no Observation
                                                        Files
                                                    </div>
                                                )}
                                                {gnssFiles &&
                                                    gnssFiles.length > 0 &&
                                                    gnssFiles.map((f) => {
                                                        return (
                                                            <div
                                                                className="flex items-center w-full shadow-lg border-[1px] border-gray-300 rounded-lg bg-neutral-content"
                                                                key={
                                                                    f.description +
                                                                    f.id
                                                                }
                                                            >
                                                                <div className="flex-grow overflow-hidden ">
                                                                    <div className="flex flex-col w-8/12 p-4 text-pretty break-words max-w-full">
                                                                        <h2 className="card-title">
                                                                            {
                                                                                f.filename
                                                                            }
                                                                        </h2>
                                                                        <p>
                                                                            {
                                                                                f.description
                                                                            }
                                                                        </p>
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        );
                                                    })}
                                            </div>
                                        )}
                                    </div>
                                </div>
                                <div className="flex flex-col items-center rounded-md bg-neutral-content">
                                    <h3
                                        className="font-bold inline-flex border-b-2 w-full justify-center 
                                                    items-center text-xl my-2"
                                    >
                                        Visit Other Files
                                        <button
                                            className="btn btn-ghost btn-circle ml-2"
                                            type="button"
                                            onClick={() => {
                                                setFileType("other");
                                                setModals({
                                                    show: true,
                                                    title: "AddFile",
                                                    type: "add",
                                                });
                                            }}
                                        >
                                            <PlusCircleIcon
                                                strokeWidth={1.5}
                                                stroke="currentColor"
                                                className="w-8 h-10"
                                            />
                                        </button>
                                    </h3>
                                    <div className="flex flex-col flex-grow w-full max-h-56 overflow-y-auto p-2">
                                        {loadingFiles ? (
                                            <div className="w-full text-center">
                                                <Spinner size="lg" />
                                            </div>
                                        ) : (
                                            <div
                                                className={`grid
                                    ${files && files.length > 1 && !loadingFiles ? "grid-cols-2 md:grid-cols-1" : "grid-cols-1"} 
                                    grid-flow-dense gap-2`}
                                            >
                                                {(!files ||
                                                    files.length === 0) && (
                                                    <div className="text-center text-neutral text-2xl font-bold w-full rounded-md bg-neutral-content p-4">
                                                        There are no other files
                                                    </div>
                                                )}
                                                {files &&
                                                    files.length > 0 &&
                                                    files.map((f) => {
                                                        return (
                                                            <div
                                                                className="flex items-center w-full shadow-lg border-[1px] border-gray-300 rounded-lg bg-neutral-content"
                                                                key={
                                                                    f.description +
                                                                    f.id
                                                                }
                                                            >
                                                                <div className="flex-grow overflow-hidden ">
                                                                    <div className="p-6 flex w-full justify-between items-center">
                                                                        <div className="flex flex-col w-8/12 text-pretty break-words max-w-full">
                                                                            <h2 className="card-title">
                                                                                {
                                                                                    f.filename
                                                                                }
                                                                            </h2>
                                                                            <p>
                                                                                {
                                                                                    f.description
                                                                                }
                                                                            </p>
                                                                        </div>
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        );
                                                    })}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </>
                        )
                    )}
                </div>
                <Alert msg={msg} />
                {loading && (
                    <div className="w-full text-center">
                        <span className="loading loading-spinner loading-lg self-center"></span>
                    </div>
                )}
                <div className="w-full flex flex-grow items-end justify-center">
                    <div className="w-8/12 flex items-end justify-end">
                        <button
                            className="btn btn-success w-6/12"
                            type={step !== 1 ? "button" : "submit"}
                            onClick={() => {
                                step === 2
                                    ? setStep(step + 1)
                                    : step === 3
                                      ? closeModal()
                                      : null;
                            }}
                            disabled={
                                loading ||
                                apiOkStatuses.includes(Number(msg?.status)) ||
                                (step === 1 && visitId !== undefined)
                            }
                        >
                            {step === 1
                                ? "Create"
                                : step === 3
                                  ? "Finish"
                                  : "Continue"}
                        </button>
                    </div>
                    <div className="w-4/12 flex items-end justify-end">
                        <button
                            type="button"
                            className="btn btn-ghost"
                            style={{
                                display:
                                    step === 1 || step === 3 ? "none" : "block",
                            }}
                            onClick={() => setStep(step + 1)}
                        >
                            <span className="font-light">skip ...</span>
                        </button>
                    </div>
                </div>
            </form>

            {modals && modals?.title === "AddFile" && (
                <AddFileModal
                    id={visitId}
                    pageType={"visit"}
                    fileType={fileType ?? ""}
                    setStateModal={setModals}
                    reFetch={() => {
                        setFileType(undefined);
                        setMsg(undefined);
                        fileType === "visitImage"
                            ? getVisitImagesById()
                            : getFiles();
                    }}
                />
            )}
            {modals && modals?.title === "EditPerson" && (
                <StationPeopleModal
                    Person={undefined}
                    modalType={modals.type}
                    setStateModal={setModals}
                    // setPerson={setPerson}
                    people={people}
                    reFetch={() => getPeople()} // En este caso closeModal es el fetch de visits que hace la pagina.
                />
            )}
        </Modal>
    );
};

export default AddVisitModal;

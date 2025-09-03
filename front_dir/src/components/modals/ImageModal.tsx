import { useEffect, useMemo, useState } from "react";
import { Modal, Spinner, Alert } from "@componentsReact";

import { useAuth, useApi } from "@hooks";
import {
    getStationImageByIdService,
    getStationVisitsImagesByIdService,
    patchVisitImagesDescription,
} from "@services";

import {
    StationImagesData,
    PatchDescriptionVisitImageResponse,
    Errors,
} from "@types";

type Photo = {
    id: number;
    actual_image: string;
    description: string;
    name: string;
};

interface Props {
    photo: Photo | undefined;
    visit?: boolean;
    closeModal: () => void;
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
    type?: "add" | "edit" | "none";
    refetch?: () => void;
}

const ImageModal = ({
    photo,
    visit,
    closeModal,
    setStateModal,
    type,
    refetch,
}: Props) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const [msg, setMsg] = useState<{
        status: number;
        msg: string;
        errors?: Errors;
    } | null>(null);

    const [success, setSuccess] = useState<boolean>(false);

    const [loading, setLoading] = useState<boolean>(false);

    const [originalPhoto, setOriginalPhoto] = useState<
        StationImagesData | undefined
    >(undefined);

    const [globalDescription, setGlobalDescription] = useState<
        string | undefined
    >(undefined);

    const handleCloseModal = () => {
        closeModal();
    };

    const getOriginalPhoto = async () => {
        try {
            setLoading(true);

            const service = visit
                ? getStationVisitsImagesByIdService
                : getStationImageByIdService;

            const res = await service<StationImagesData>(api, photo?.id ?? 0);

            if (res.actual_image) {
                setOriginalPhoto(res);
                if (res.description) {
                    const description = res.description;
                    setGlobalDescription(description);
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const updatePhotoDescription = async () => {
        setLoading(true);
        try {
            if (globalDescription !== undefined) {
                const body = {
                    description: globalDescription,
                };
                if (typeof originalPhoto?.id === "number") {
                    const res =
                        await patchVisitImagesDescription<PatchDescriptionVisitImageResponse>(
                            api,
                            body,
                            originalPhoto?.id,
                        );

                    if (res.statusCode !== 200) {
                        setMsg({
                            status: 400,
                            errors: {
                                errors: [
                                    {
                                        code: "400",
                                        attr: "files",
                                        detail: "",
                                    },
                                ],
                                type: "error",
                            },
                            msg: "Files were not uploaded successfully",
                        });
                    } else {
                        setMsg({
                            status: 200,
                            msg: "Photo description updated successfully",
                        });
                    }
                }
            }
        } catch (err) {
            setMsg({
                status: 400,
                errors: {
                    errors: [
                        {
                            code: "400",
                            attr: "files",
                            detail: "",
                        },
                    ],
                    type: "error",
                },
                msg: "Files were not uploaded successfully",
            });
        } finally {
            setLoading(false);
            setSuccess(true);
        }
    };

    const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        updatePhotoDescription();
        refetch && refetch();
    };

    useEffect(() => {
        getOriginalPhoto();
    }, [photo]);

    const image = useMemo(() => {
        return (
            <Modal
                close={false}
                modalId={"ViewStationPhoto"}
                size={"md"}
                handleCloseModal={() => handleCloseModal()}
                setModalState={setStateModal}
            >
                {!originalPhoto ? (
                    <div className="flex flex-col flex-grow w-full items-center py-4">
                        <span className="font-semibold text-xl mb-12">
                            Loading image...
                        </span>
                        <Spinner size={"lg"} />
                    </div>
                ) : (
                    originalPhoto?.name && (
                        <div className="space-y-4">
                            <img
                                className="w-full h-fit object-contain"
                                src={
                                    "data:image/png;base64," +
                                    originalPhoto?.actual_image
                                }
                                alt={"photo" + originalPhoto?.name}
                            />
                            {originalPhoto?.description && type !== "none" && (
                                <p className="break-words border-t-2 border-neutral-300 pt-3 leading-6 tracking-tight text-xl font-semibold">
                                    {originalPhoto?.description}
                                </p>
                            )}
                            {type === "none" && (
                                <form
                                    className="flex flex-col items-center space-y-2"
                                    onSubmit={handleSubmit}
                                >
                                    <label
                                        htmlFor="description"
                                        className="text-lg font-semibold"
                                    >
                                        Description
                                    </label>
                                    <input
                                        type="text"
                                        value={
                                            globalDescription
                                                ? globalDescription
                                                : ""
                                        }
                                        className="rounded-md text-md input input-sm input-bordered w-full"
                                        onChange={(e) =>
                                            setGlobalDescription(e.target.value)
                                        }
                                    />
                                    <button
                                        type="submit"
                                        disabled={success}
                                        className="btn btn-success mt-2 w-full"
                                    >
                                        Submit
                                    </button>
                                    {loading && (
                                        <div className="flex flex-col items-center justify-center space-y-4 w-full">
                                            <Spinner size="lg" />
                                        </div>
                                    )}
                                    {msg && <Alert msg={msg} />}
                                </form>
                            )}
                        </div>
                    )
                )}
            </Modal>
        );
    }, [originalPhoto, loading, globalDescription]);
    return image;
};

export default ImageModal;

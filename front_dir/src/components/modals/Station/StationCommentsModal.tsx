import { ArrowUpRightIcon } from "@heroicons/react/24/outline";
import {
    StationData,
    StationMetadataServiceData,
    StationVisitsData,
} from "@types";
import Modal from "../Modal";
import { Link } from "react-router-dom";
import StationMetadataModal from "./StationMetadataModal";
import { useEffect, useState } from "react";
import { showModal } from "@utils/index";

interface Props {
    visits: StationVisitsData[] | undefined;
    stationMeta: StationMetadataServiceData | undefined;
    station: StationData | undefined;
    setModal: React.Dispatch<
        React.SetStateAction<
            | {
                  show: boolean;
                  title: string;
                  type: "add" | "edit" | "none";
              }
            | undefined
        >
    >;
    onHide: () => void;
}

const StationCommentsModal = ({
    visits,
    stationMeta,
    station,
    onHide,
    setModal,
}: Props) => {
    const [modals, setModals] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    useEffect(() => {
        modals?.show && showModal(modals.title);
    }, [modals]);

    return (
        <Modal
            close={true}
            modalId={"StationComments"}
            size={"lg"}
            handleCloseModal={() => onHide()}
            setModalState={setModal}
        >
            <h1 className=" pb-2 text-lg opacity-60 tracking-wide">Metadata</h1>
            {stationMeta && stationMeta.comments !== "" ? (
                // <li className="list-row flex rounded-md p-6 mb-2 border border-gray-300 place-content-between hover:bg-gray-200">
                <li className="list-row flex rounded-sm place-content-between hover:bg-gray-200 p-[10px] mb-[10px]">
                    <div
                        dangerouslySetInnerHTML={{
                            __html: stationMeta.comments,
                        }}
                    ></div>

                    <button
                        onClick={() => {
                            setModals({
                                show: true,
                                title: "Metadata",
                                type: "none",
                            });
                        }}
                        className="btn btn-square btn-ghost self-center ml-4"
                    >
                        <ArrowUpRightIcon className="size-[1.2em]" />
                    </button>
                    {modals?.show && modals.title === "Metadata" && (
                        <StationMetadataModal
                            close={false}
                            station={station}
                            stationMetaMain={stationMeta}
                            size={"xl"}
                            refetch={() => {}}
                            setModalState={setModal}
                        />
                    )}
                </li>
            ) : (
                <div className="text-center text-neutral text-xl font-bold w-full rounded-md bg-neutral-content p-6 mb-2">
                    There are no Comments
                </div>
            )}

            <h1 className=" pb-2 text-lg opacity-60 tracking-wide">Visits</h1>
            {visits && visits.length > 0 ? (
                visits?.map((v: StationVisitsData) => {
                    const stationNetwork = v?.station_network_code;
                    const stationCode = v?.station_station_code;

                    return (
                        <li className="list-row flex rounded-md p-6 mb-2 border border-gray-300 place-content-between hover:bg-gray-200">
                            <div className="w-lg">
                                <strong>Visit {v.date}</strong>
                                <div
                                    className="overflow-auto"
                                    dangerouslySetInnerHTML={{
                                        __html: v.comments,
                                    }}
                                ></div>
                            </div>
                            <Link
                                className="btn btn-square btn-ghost self-center"
                                key={v.id}
                                to={`/${stationNetwork}/${stationCode}/visits`}
                                state={{
                                    visitDetail: {
                                        ...v,
                                        api_id: v.station,
                                    },
                                }}
                            >
                                <ArrowUpRightIcon className="size-[1.2em]" />
                            </Link>
                        </li>
                    );
                })
            ) : (
                <div className="text-center text-neutral text-xl font-bold w-full rounded-md bg-neutral-content p-6 mb-2">
                    There are no Comments
                </div>
            )}
        </Modal>
    );
};

export default StationCommentsModal;

import { useLocation, useParams } from "react-router-dom";
import { PdfContainer } from "@componentsReact";

import {
    ArrowPathIcon,
    ChatBubbleLeftEllipsisIcon,
    ExclamationCircleIcon,
    GlobeAsiaAustraliaIcon,
} from "@heroicons/react/24/outline";

import { hasDifferences } from "@utils";
import {
    Errors,
    StationData,
    StationMetadataServiceData,
    StationVisitsData,
} from "@types";

interface Props {
    functions: {
        setMessage: React.Dispatch<
            React.SetStateAction<{
                error: boolean | undefined;
                msg: string;
                errors?: Errors;
            }>
        >;
        setLoadPdf: React.Dispatch<React.SetStateAction<boolean>>;
        setLoadedPdfData: React.Dispatch<
            React.SetStateAction<boolean | undefined>
        >;
        getButtonClasses: () => string;
        getKmzBalloon: () => void;
        getReStation: () => void;
        setModals: React.Dispatch<
            React.SetStateAction<
                | {
                      show: boolean;
                      title: string;
                      type: "add" | "edit" | "none";
                  }
                | undefined
            >
        >;
    };
    constants: {
        station: StationData | undefined;
        reLoading: boolean;
        reStation: StationData | undefined;
        stationMeta: StationMetadataServiceData | undefined;
        visits: StationVisitsData[] | undefined;
        loadPdf: boolean;
        loadedMap: boolean | undefined;
        errorMessages: string[];
        stationLocationScreen: string;
        stationLocationDetailScreen: string;
    };
}

const StationButtons = ({ functions, constants }: Props) => {
    const { nc, sc } = useParams();

    const location = useLocation();

    const {
        setMessage,
        setLoadPdf,
        setLoadedPdfData,
        getButtonClasses,
        getKmzBalloon,
        getReStation,
        setModals,
    } = functions;

    const {
        station,
        reLoading,
        reStation,
        stationMeta,
        visits,
        loadPdf,
        loadedMap,
        errorMessages,
        stationLocationScreen,
        stationLocationDetailScreen,
    } = constants;

    const definitiveStation =
        station && reStation && hasDifferences(station, reStation)
            ? reStation
            : station;

    return (
        <div className="flex items-center justify-start min-w-[100px] gap-0 absolute -right-[145px] top-3">
            {location.pathname === `/${nc}/${sc}` && (
                <>
                    <PdfContainer
                        station={definitiveStation}
                        stationMeta={stationMeta}
                        visits={visits}
                        loadPdf={loadPdf}
                        stationLocationScreen={stationLocationScreen}
                        stationLocationDetailScreen={
                            stationLocationDetailScreen
                        }
                        loadedMap={loadedMap}
                        // loadPdfdata={loadPdfData}
                        setMessage={setMessage}
                        setLoadPdf={setLoadPdf}
                        setLoadedPdfData={setLoadedPdfData}
                    />
                    <button
                        className={
                            "flex items-center justify-center " +
                            getButtonClasses()
                        }
                        title="Download station kmz"
                        onClick={(e) => {
                            e.preventDefault();
                            getKmzBalloon();
                        }}
                    >
                        <GlobeAsiaAustraliaIcon className="size-6" />
                    </button>
                    <button
                        className={
                            "flex items-center justify-center " +
                            getButtonClasses()
                        }
                        title="View all comments"
                        onClick={(e) => {
                            e.preventDefault();
                            setModals &&
                                setModals({
                                    show: true,
                                    title: "StationComments",
                                    type: "none",
                                });
                        }}
                    >
                        <ChatBubbleLeftEllipsisIcon className="size-6" />
                    </button>
                </>
            )}

            {location.pathname === `/${nc}/${sc}/rinex` &&
                errorMessages.length > 0 && (
                    <div className="indicator">
                        <ExclamationCircleIcon
                            className={`size-6 fill-red-500`}
                            title={errorMessages.join("\n")}
                        />
                    </div>
                )}
            <button
                className={getButtonClasses()}
                disabled={reLoading}
                onClick={getReStation}
                title="Fetch gaps status"
            >
                <ArrowPathIcon className="size-6" />
            </button>
        </div>
    );
};

export default StationButtons;

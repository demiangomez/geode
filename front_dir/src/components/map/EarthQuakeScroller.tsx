import React, { useState, useEffect, useCallback, useRef } from "react";
import { Spinner, Toast } from "@componentsReact";

import { useAuth, useApi, usePopup } from "@hooks";
import { ArrowDownTrayIcon, ClipboardIcon } from "@heroicons/react/24/outline";

import { formattedDates } from "@utils";
import { removeEarthquakesAffectedStationsCache } from "@services";

import {
    EarthquakeData,
    StationsAffectedServiceData,
    ErrorResponse,
} from "@types";
interface EarthQuakeScrollerProps {
    forceSyncMapScroller: number;
    earthquakes: EarthquakeData[];
    earthquakeChosen: EarthquakeData | undefined;
    handleEarthquakeState: (earthquake: EarthquakeData) => void;
    handleEarthquakeClose: () => void;
    scrollerCondition: boolean;
    spinner: boolean;
    earthquakeAffectedStations: StationsAffectedServiceData | undefined;
    setToggleEarthquakeMask: React.Dispatch<React.SetStateAction<boolean>>;
    setToggleCoseismicVector: React.Dispatch<React.SetStateAction<boolean>>;
    vectorMagnitude: number;
    setVectorMagnitude: React.Dispatch<React.SetStateAction<number>>;
}

const EarthQuakeScroller: React.FC<EarthQuakeScrollerProps> = ({
    forceSyncMapScroller,
    earthquakes,
    earthquakeChosen,
    handleEarthquakeState,
    handleEarthquakeClose,
    spinner,
    scrollerCondition,
    earthquakeAffectedStations,
    setToggleEarthquakeMask,
    setToggleCoseismicVector,
    vectorMagnitude,
    setVectorMagnitude,
}) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);
    const disableDisplacements =
        earthquakeAffectedStations?.coseismic_displacements &&
        earthquakeAffectedStations?.coseismic_displacements.length === 0;
    //---------------------------------------------------------UseState-------------------------------------------------------------
    const [forceRenderContainer, setForceRenderContainer] = useState(0);

    const [toggleState, setToggleState] = useState<boolean>(true);

    const [toggleVector, setToggleVector] = useState<boolean>(false);

    const [sortedEarthquakes, setSortedEarthquakes] = useState<
        EarthquakeData[]
    >([]);

    const [toastVisible, setToastVisible] = useState(false);
    const [toastMessage, setToastMessage] = useState("");
    const [toastError, setToastError] = useState(false);

    const toastTimerRef = useRef<number | null>(null);

    const { show, showPopup } = usePopup(2000);

    const [copyId, setCopyId] = useState<string | null>(null);

    //---------------------------------------------------------Funciones-------------------------------------------------------------
    const isStateTrue = (earthquake: EarthquakeData) => {
        if (earthquakeChosen?.api_id === earthquake.api_id) {
            return true;
        }
        return false;
    };

    const handleToggleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const isChecked = e.target.checked;
        setToggleState(isChecked);
        setToggleEarthquakeMask(isChecked);

        try {
            const stored = localStorage.getItem("earthquakeChosen");
            const parsed = stored ? JSON.parse(stored) : null;
            if (
                parsed &&
                earthquakeChosen &&
                parsed.api_id === earthquakeChosen.api_id
            ) {
                const merged = { ...parsed, ui_toggle_mask: isChecked };
                localStorage.setItem(
                    "earthquakeChosen",
                    JSON.stringify(merged),
                );
            } else if (earthquakeChosen) {
                const merged = {
                    ...earthquakeChosen,
                    ui_toggle_mask: isChecked,
                };
                localStorage.setItem(
                    "earthquakeChosen",
                    JSON.stringify(merged),
                );
            }
        } catch (err) {
            console.error(
                "Failed to persist earthquakeChosen toggle mask",
                err,
            );
        }
    };

    const handleToggleVector = (e: React.ChangeEvent<HTMLInputElement>) => {
        const isChecked = e.target.checked;
        setToggleVector(isChecked);
        setToggleCoseismicVector(isChecked);

        try {
            const stored = localStorage.getItem("earthquakeChosen");
            const parsed = stored ? JSON.parse(stored) : null;
            if (
                parsed &&
                earthquakeChosen &&
                parsed.api_id === earthquakeChosen.api_id
            ) {
                const merged = { ...parsed, ui_toggle_vector: isChecked };
                localStorage.setItem(
                    "earthquakeChosen",
                    JSON.stringify(merged),
                );
            } else if (earthquakeChosen) {
                const merged = {
                    ...earthquakeChosen,
                    ui_toggle_vector: isChecked,
                };
                localStorage.setItem(
                    "earthquakeChosen",
                    JSON.stringify(merged),
                );
            }
        } catch (err) {
            console.error(
                "Failed to persist earthquakeChosen toggle vector",
                err,
            );
        }
    };

    const downloadFile = (
        data: string | undefined,
        filename: string,
        fileType: "kml" | "csv",
    ) => {
        if (!data) {
            console.warn(`No data available for ${filename}`);
            return;
        }

        let dataUrl: string;

        if (fileType === "kml") {
            dataUrl = `data:application/octet-stream;base64,${data}`;
        } else {
            const blob = new Blob([data], { type: "text/csv;charset=utf-8;" });
            dataUrl = URL.createObjectURL(blob);
        }

        const downloadLink = document.createElement("a");
        downloadLink.href = dataUrl;
        downloadLink.download = filename;

        document.body.appendChild(downloadLink);
        downloadLink.click();
        document.body.removeChild(downloadLink);

        if (fileType === "csv") {
            URL.revokeObjectURL(dataUrl);
        }
    };

    const deleteCacheEarthquakes = async () => {
        try {
            const res =
                await removeEarthquakesAffectedStationsCache<ErrorResponse>(
                    api,
                );
            if (res?.statusCode === 201) {
                setToastMessage("Earthquake cache cleared successfully.");
            }
            setToastError(false);
            setToastVisible(true);

            if (toastTimerRef.current)
                window.clearTimeout(toastTimerRef.current);
            toastTimerRef.current = window.setTimeout(() => {
                setToastVisible(false);
                toastTimerRef.current = null;
            }, 1500);
        } catch (error) {
            setToastMessage("Failed to clear earthquake cache.");
            setToastError(true);
            setToastVisible(true);

            if (toastTimerRef.current)
                window.clearTimeout(toastTimerRef.current);
            toastTimerRef.current = window.setTimeout(() => {
                setToastVisible(false);
                toastTimerRef.current = null;
            }, 3000);

            console.error(error);
        }
    };

    //---------------------------------------------------------UseCallback-------------------------------------------------------------
    const sortEarthquakes = useCallback(
        (sortBy: string) => {
            const newSorted = [...earthquakes];
            if (sortBy === "date-") {
                newSorted.sort(
                    (a, b) =>
                        new Date(a.date).getTime() - new Date(b.date).getTime(),
                );
            } else if (sortBy === "date+") {
                newSorted.sort(
                    (a, b) =>
                        new Date(b.date).getTime() - new Date(a.date).getTime(),
                );
            } else if (sortBy === "mag+") {
                newSorted.sort((a, b) => b.mag - a.mag);
            } else if (sortBy === "depth+") {
                newSorted.sort((a, b) => b.depth - a.depth);
            }
            setSortedEarthquakes(newSorted);
        },
        [earthquakes],
    );

    //---------------------------------------------------------UseEffect-------------------------------------------------------------
    useEffect(() => {
        setForceRenderContainer((prev) => prev + 1);
    }, [earthquakeChosen, sortedEarthquakes]);

    useEffect(() => {
        setSortedEarthquakes(earthquakes);
    }, [earthquakes]);

    useEffect(() => {
        return () => {
            if (toastTimerRef.current) {
                window.clearTimeout(toastTimerRef.current);
            }
        };
    }, []);

    useEffect(() => {
        try {
            const stored = localStorage.getItem("earthquakeChosen");
            const parsed = stored ? JSON.parse(stored) : null;
            if (
                parsed &&
                earthquakeChosen &&
                parsed.api_id === earthquakeChosen.api_id
            ) {
                const mask =
                    typeof parsed.ui_toggle_mask === "boolean"
                        ? parsed.ui_toggle_mask
                        : true;
                const vector =
                    typeof parsed.ui_toggle_vector === "boolean"
                        ? parsed.ui_toggle_vector
                        : false;
                setVectorMagnitude(vectorMagnitude);
                setToggleState(mask);
                setToggleVector(vector);
                setToggleEarthquakeMask(mask);
                setToggleCoseismicVector(vector);
            } else {
                setVectorMagnitude(vectorMagnitude);
                setToggleState(true);
                setToggleVector(false);
                setToggleEarthquakeMask(true);
                setToggleCoseismicVector(false);
            }
        } catch (err) {
            console.error("Failed to restore earthquakeChosen toggles", err);
        }
    }, [earthquakeChosen]);

    //---------------------------------------------------------Return-------------------------------------------------------------

    return (
        <>
            {scrollerCondition ? (
                <div
                    id="controller"
                    className="z-[100000] max-h-[92vh] w-[20vw] scrollbar-thin overflow-y-auto overflow-x-hidden absolute top-0 left-0"
                >
                    <div className="overflow-y-auto min-h-[92vh] max-h-full h-auto bg-white rounded-md border-t border-l border-b border-gray-400 overflow-x-hidden">
                        {spinner ? (
                            <div className="flex items-center justify-center min-h-[92vh]">
                                <Spinner size="lg" />
                            </div>
                        ) : (
                            <div className="flex justify-end mr-2">
                                <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    fill="none"
                                    viewBox="0 0 24 24"
                                    strokeWidth={1.5}
                                    stroke="currentColor"
                                    className="size-6 cursor-pointer mt-2 mr-1 hover:bg-gray-200 hover:rounded-full hover:shadow-md"
                                    onClick={() => {
                                        handleEarthquakeClose();
                                    }}
                                >
                                    <path
                                        strokeLinecap="round"
                                        strokeLinejoin="round"
                                        d="M6 18 18 6M6 6l12 12"
                                    />
                                </svg>
                            </div>
                        )}
                        {!spinner && (
                            <div className="flex justify-between mb-3 mr-3 ml-3">
                                <div className="">
                                    <div className="font-bold text-xl">
                                        <h2>Search results</h2>
                                    </div>
                                    <div className="">
                                        {earthquakes.length + " earthquakes."}
                                    </div>
                                    <div
                                        className="cursor-pointer underline hover:text-black"
                                        onClick={() => {
                                            deleteCacheEarthquakes();
                                        }}
                                    >
                                        {"Clear cache"}
                                    </div>
                                    {toastVisible && (
                                        <Toast
                                            msg={toastMessage}
                                            error={toastError}
                                        />
                                    )}
                                </div>
                                <div className="flex justify-center items-start flex-col">
                                    <div>
                                        <span>Sort by</span>
                                    </div>
                                    <div>
                                        <select
                                            className="border bg-white border-gray-400 rounded-md p-1"
                                            onChange={(e) =>
                                                sortEarthquakes(e.target.value)
                                            }
                                        >
                                            <option value="none">
                                                Select an option
                                            </option>
                                            <option value="date+">
                                                Newest
                                            </option>
                                            <option value="date-">
                                                Oldest
                                            </option>
                                            <option value="mag+">
                                                Magnitude
                                            </option>
                                            <option value="depth+">
                                                Depth
                                            </option>
                                        </select>
                                    </div>
                                </div>
                            </div>
                        )}
                        {!spinner &&
                            sortedEarthquakes
                                ?.sort((a, b) => {
                                    const aState = isStateTrue(a);
                                    const bState = isStateTrue(b);
                                    if (aState && !bState) return -1;
                                    if (!aState && bState) return 1;
                                    return 0;
                                })
                                .map((earthquake) => (
                                    <div
                                        key={
                                            forceRenderContainer +
                                            earthquake.api_id +
                                            forceSyncMapScroller
                                        }
                                        onClick={() => {
                                            handleEarthquakeState(earthquake);
                                        }}
                                        className={
                                            isStateTrue(earthquake)
                                                ? "label cursor-pointer border border-gray-950 bg-slate-400 flex items-center justify-start flex-row p-2"
                                                : "label cursor-pointer border border-gray-400 flex items-center justify-start flex-row p-2"
                                        }
                                        id={earthquake.api_id.toString()}
                                    >
                                        <div className="flex items-start gap-4 m-2 mr-6">
                                            <div>
                                                {earthquake.mag.toFixed(1)}
                                            </div>
                                            <div className="flex flex-col">
                                                <span className="font-bold mr-2 break-words">
                                                    {earthquake.location}
                                                </span>
                                                <div>
                                                    <span className="mr-2 truncate">
                                                        {formattedDates(
                                                            earthquake.date,
                                                        )}
                                                    </span>
                                                    <span>
                                                        {earthquake.depth +
                                                            "km"}
                                                    </span>
                                                </div>
                                                <div>
                                                    <span>{earthquake.id}</span>
                                                </div>
                                                {isStateTrue(earthquake) ? (
                                                    <div className="mt-4">
                                                        <div>
                                                            <div className="flex items-center justify-between">
                                                                <span className="font-bold">
                                                                    Masks
                                                                </span>
                                                                <input
                                                                    type="checkbox"
                                                                    className={`toggle`}
                                                                    style={{
                                                                        borderRadius:
                                                                            "50px",
                                                                    }}
                                                                    checked={
                                                                        toggleState
                                                                    }
                                                                    onChange={
                                                                        handleToggleChange
                                                                    }
                                                                    onClick={(
                                                                        e,
                                                                    ) =>
                                                                        e.stopPropagation()
                                                                    }
                                                                />
                                                            </div>
                                                            <div className="text-xs text-gray-600 mb-2 text-right">
                                                                {toggleState
                                                                    ? "Coseismic + Postseismic"
                                                                    : "Coseismic only"}
                                                            </div>
                                                            <div className="grid grid-cols-2 gap-4">
                                                                {toggleState ? (
                                                                    <>
                                                                        <div className="flex flex-col items-center justify-center">
                                                                            {/* <span className="font-semibold text-sm">
                                                                                Postseismic
                                                                            </span> */}
                                                                            <button
                                                                                className="btn btn-ghost btn-circle"
                                                                                title="Download Postseismic KML"
                                                                                onClick={(
                                                                                    e,
                                                                                ) => {
                                                                                    e.stopPropagation();
                                                                                    downloadFile(
                                                                                        earthquakeAffectedStations?.kml_including_postseismic,
                                                                                        `${earthquakeChosen?.id}.kml`,
                                                                                        "kml",
                                                                                    );
                                                                                }}
                                                                            >
                                                                                <ArrowDownTrayIcon className="size-6" />
                                                                            </button>
                                                                        </div>
                                                                    </>
                                                                ) : (
                                                                    <div className="flex flex-col items-center justify-center">
                                                                        {/* <span className="font-semibold text-sm">
                                                                            Coseismic
                                                                        </span> */}
                                                                        <button
                                                                            className="btn btn-ghost btn-circle"
                                                                            title="Download Coseismic KML"
                                                                            onClick={(
                                                                                e,
                                                                            ) => {
                                                                                e.stopPropagation();
                                                                                downloadFile(
                                                                                    earthquakeAffectedStations?.kml_without_postseismic,
                                                                                    `${earthquakeChosen?.id}.kml`,
                                                                                    "kml",
                                                                                );
                                                                            }}
                                                                        >
                                                                            <ArrowDownTrayIcon className="size-6" />
                                                                        </button>
                                                                    </div>
                                                                )}
                                                            </div>
                                                        </div>
                                                        <div className="mt-4">
                                                            <div className="flex items-center justify-between">
                                                                <span className="font-bold">
                                                                    Stations
                                                                    Affected
                                                                </span>

                                                                <div className="flex items-center justify-end">
                                                                    <input
                                                                        disabled={
                                                                            disableDisplacements
                                                                        }
                                                                        type="checkbox"
                                                                        className={`toggle`}
                                                                        style={{
                                                                            borderRadius:
                                                                                "50px",
                                                                        }}
                                                                        checked={
                                                                            toggleVector
                                                                        }
                                                                        onChange={
                                                                            handleToggleVector
                                                                        }
                                                                        onClick={(
                                                                            e,
                                                                        ) =>
                                                                            e.stopPropagation()
                                                                        }
                                                                    />
                                                                </div>
                                                            </div>

                                                            <div className="text-xs text-gray-600 mb-2 text-right">
                                                                Show
                                                                displacements
                                                            </div>

                                                            {/* Coseismic Section */}
                                                            {toggleState ? (
                                                                <div className="mt-3">
                                                                    {/* <div className="text-sm font-semibold text-gray-700 ml-4 mb-2">
                                                                        Coseismic
                                                                        +
                                                                        Postseismic
                                                                    </div> */}
                                                                    <div className="grid grid-cols-2 gap-4 justify-items-center">
                                                                        <button
                                                                            className="btn btn-ghost btn-circle"
                                                                            title="Download Coseismic + Postseismic CSV"
                                                                            onClick={(
                                                                                e,
                                                                            ) => {
                                                                                e.stopPropagation();
                                                                                downloadFile(
                                                                                    earthquakeAffectedStations?.csv_including_postseismic,
                                                                                    `${earthquakeChosen?.id}.csv`,
                                                                                    "csv",
                                                                                );
                                                                            }}
                                                                        >
                                                                            <ArrowDownTrayIcon className="size-6" />
                                                                        </button>
                                                                        <button
                                                                            className={` ${showPopup && copyId === "postseismic" ? "tooltip tooltip-open" : ""} mr-2`}
                                                                            title="Copy Coseismic + Postseismic CSV"
                                                                            data-tip="Copied!"
                                                                        >
                                                                            <ClipboardIcon
                                                                                className="size-6 cursor-pointer rounded-md transition-all duration-75 btn-ghost"
                                                                                onClick={(
                                                                                    e,
                                                                                ) => {
                                                                                    e.stopPropagation();
                                                                                    if (
                                                                                        earthquakeAffectedStations?.csv_including_postseismic
                                                                                    )
                                                                                        navigator.clipboard.writeText(
                                                                                            earthquakeAffectedStations.csv_including_postseismic,
                                                                                        );
                                                                                    setCopyId(
                                                                                        "postseismic",
                                                                                    );
                                                                                    show();
                                                                                }}
                                                                            />
                                                                        </button>
                                                                    </div>
                                                                </div>
                                                            ) : (
                                                                // Show only Coseismic if toggleState is false
                                                                <div className="mt-2">
                                                                    {/* <div className="text-sm font-semibold text-gray-700 ml-4 mb-2">
                                                                        Coseismic
                                                                    </div> */}
                                                                    <div className="grid grid-cols-2 gap-4 justify-items-center">
                                                                        <button
                                                                            className="btn btn-ghost btn-circle"
                                                                            title="Download Coseismic CSV"
                                                                            onClick={(
                                                                                e,
                                                                            ) => {
                                                                                e.stopPropagation();
                                                                                downloadFile(
                                                                                    earthquakeAffectedStations?.csv_without_postseismic,
                                                                                    `${earthquakeChosen?.id}.csv`,
                                                                                    "csv",
                                                                                );
                                                                            }}
                                                                        >
                                                                            <ArrowDownTrayIcon className="size-6" />
                                                                        </button>
                                                                        <button
                                                                            className={` ${showPopup && copyId === "coseismic" ? "tooltip tooltip-open" : ""} mr-2`}
                                                                            title="Copy Coseismic CSV"
                                                                            data-tip="Copied!"
                                                                        >
                                                                            <ClipboardIcon
                                                                                className="size-6 cursor-pointer rounded-md transition-all duration-75 btn-ghost"
                                                                                onClick={(
                                                                                    e,
                                                                                ) => {
                                                                                    e.stopPropagation();
                                                                                    if (
                                                                                        earthquakeAffectedStations?.csv_without_postseismic
                                                                                    )
                                                                                        navigator.clipboard.writeText(
                                                                                            earthquakeAffectedStations.csv_without_postseismic,
                                                                                        );
                                                                                    setCopyId(
                                                                                        "coseismic",
                                                                                    );
                                                                                    show();
                                                                                }}
                                                                            />
                                                                        </button>
                                                                    </div>
                                                                </div>
                                                            )}
                                                        </div>
                                                    </div>
                                                ) : null}
                                            </div>
                                        </div>
                                    </div>
                                ))}
                    </div>
                </div>
            ) : null}
        </>
    );
};
export default EarthQuakeScroller;

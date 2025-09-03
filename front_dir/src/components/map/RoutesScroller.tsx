import { Scroller } from "@componentsReact";
import { StationVisitsData, StationMetadataServiceData } from "@types";
import { getRandomColor, possibleColors } from "@utils";
import React, { useEffect, useState } from "react";

interface RoutesScrollerProps {
    map?: L.Map | null;
    visits: StationVisitsData[];
    changeKml: VisitsStates[];
    changeMeta: boolean;
    setChangeKml: React.Dispatch<React.SetStateAction<VisitsStates[]>>;
    setChangeMeta: React.Dispatch<React.SetStateAction<boolean>>;
    stationMeta: StationMetadataServiceData;
    showScroller: boolean;
    setShowScroller: React.Dispatch<React.SetStateAction<boolean>>;
}

interface VisitsStates {
    visitId: number;
    checked: boolean;
    color: string;
}

const RoutesScroller = ({
    map,
    visits,
    changeKml,
    changeMeta,
    setChangeKml,
    setChangeMeta,
    stationMeta,
    showScroller,
    setShowScroller,
}: RoutesScrollerProps) => {
    const getColor = (visit: StationVisitsData) => {
        const visitColor = changeKml.find(
            (visitBool) => visitBool.visitId === visit.id,
        );
        if (visitColor) {
            return visitColor.color;
        }
        return "black";
    };

    const adjustIndex = (index: number) => {
        const exponente = index % 7 === 0 ? index : Math.floor(index / 7);

        const finalNumber =
            index > 7 ? index - exponente * possibleColors.length : index;

        return finalNumber;
    };

    //--------------------------------------------------------useState--------------------------------------------------------

    const [isAllSelected, setIsAllSelected] = useState<boolean>(false);

    //--------------------------------------------------------UseEffect--------------------------------------------------------
    useEffect(() => {
        if (changeKml.length === 0) {
            if (visits.length > 0) {
                const newChangeKml: VisitsStates[] = [];

                visits?.forEach((visit, index) => {
                    const newKml = {
                        visitId: visit.id,
                        checked: false,
                        color: getRandomColor(adjustIndex(index)),
                    };
                    newChangeKml.push(newKml);
                });

                setChangeKml(newChangeKml);
            }
        }
    }, [showScroller, changeKml.length, setChangeKml, visits]);

    useEffect(() => {
        if (
            changeKml.length > 0 &&
            changeKml.every((visitBool) => visitBool.checked) &&
            (stationMeta.navigation_filename ? changeMeta : true) //Si hay un file el changeMeta dice si esta checkeado, si no hay no lo considera
        ) {
            setIsAllSelected(true);
        }

        if (visits.length === 0) {
            setIsAllSelected(changeMeta);
        }
    }, [changeKml, changeMeta]);

    //--------------------------------------------------------Return--------------------------------------------------------
    return (
        <>
            <Scroller
                map={map}
                buttonCondition={
                    (Array.isArray(visits) &&
                        visits.length > 0 &&
                        visits.some((visit) => visit.navigation_actual_file)) ||
                    !!stationMeta?.navigation_actual_file
                }
                scrollerCondition={visits && showScroller}
                scrollerName="Show routes"
                showScroller={showScroller}
                setShowScroller={setShowScroller}
                fromMain={false}
            >
                <div className="form-control">
                    <label className="label cursor-pointer truncate">
                        <span className="font-bold mr-4">Select All</span>
                        <input
                            type="checkbox"
                            className="checkbox checkbox-sm"
                            checked={isAllSelected}
                            onChange={(e) => {
                                if (changeKml.length !== 0) {
                                    setChangeMeta(e.target.checked);
                                    setChangeKml((prev) => {
                                        return prev.map((visitBool) => ({
                                            ...visitBool,
                                            checked: e.target.checked,
                                        }));
                                    });
                                } else if (changeKml.length === 0) {
                                    setChangeMeta(e.target.checked);
                                    if (e.target.checked) {
                                        setChangeKml(
                                            visits.map((visit) => ({
                                                visitId: visit.id,
                                                color: getColor(visit),
                                                checked: true,
                                            })),
                                        );
                                    } else {
                                        setChangeKml(
                                            visits.map((visit) => ({
                                                visitId: visit.id,
                                                color: getColor(visit),
                                                checked: false,
                                            })),
                                        );
                                    }
                                }
                            }}
                        />
                    </label>
                </div>
                {stationMeta?.navigation_actual_file && (
                    <div className="form-control">
                        <label
                            className="cursor-pointer"
                            style={{
                                display: "flex",
                                userSelect: "none",
                                alignItems: "center",
                                justifyContent: "start",
                                paddingLeft: "0.25rem",
                                paddingRight: "0.25rem",
                                paddingTop: "0.5rem",
                                paddingBottom: "0.5rem",
                            }}
                        >
                            <div
                                style={{
                                    backgroundColor: "black",
                                    width: "20px",
                                    height: "20px",
                                    borderRadius: "10px",
                                    marginRight: "5px",
                                }}
                            ></div>
                            <span className="font-bold mr-4">Default</span>
                            <input
                                type="checkbox"
                                className="checkbox checkbox-sm"
                                checked={changeMeta}
                                onChange={(e) => {
                                    setChangeMeta(e.target.checked);
                                }}
                                style={{ marginLeft: "auto" }}
                            />
                        </label>
                    </div>
                )}
                {visits.map(
                    (visit, index) =>
                        visit.navigation_actual_file && (
                            <div className="form-control" key={visit.id}>
                                <label className="label cursor-pointer">
                                    <div
                                        style={{
                                            backgroundColor: getColor(visit),
                                            width: "20px",
                                            height: "20px",
                                            borderRadius: "10px",
                                            marginRight: "5px",
                                        }}
                                    ></div>
                                    <span
                                        className="font-bold mr-2 truncate"
                                        title={
                                            visit.campaign_name && visit.date
                                                ? visit.campaign_name +
                                                  " - " +
                                                  visit.date
                                                : visit.date
                                        }
                                    >
                                        {visit.date && "Visit - " + visit.date}
                                    </span>
                                    <input
                                        type="checkbox"
                                        className="checkbox checkbox-sm"
                                        checked={changeKml.some(
                                            (visitBool) =>
                                                visitBool.visitId ===
                                                    visit.id &&
                                                visitBool.checked,
                                        )}
                                        onChange={(e) => {
                                            setChangeKml((prev) => {
                                                const visitToMod = prev.find(
                                                    (visitBool) =>
                                                        visitBool.visitId ===
                                                        visit.id,
                                                );
                                                if (visitToMod) {
                                                    return prev.map(
                                                        (visitBool) =>
                                                            visitBool.visitId ===
                                                            visit.id
                                                                ? {
                                                                      ...visitBool,
                                                                      checked:
                                                                          e
                                                                              .target
                                                                              .checked,
                                                                  }
                                                                : visitBool,
                                                    );
                                                } else {
                                                    return [
                                                        ...prev,
                                                        {
                                                            visitId: visit.id,
                                                            checked:
                                                                e.target
                                                                    .checked,
                                                            color: getRandomColor(
                                                                adjustIndex(
                                                                    index,
                                                                ),
                                                            ),
                                                        },
                                                    ];
                                                }
                                            });
                                        }}
                                    />
                                </label>
                            </div>
                        ),
                )}
            </Scroller>
        </>
    );
};

export default React.memo(RoutesScroller);

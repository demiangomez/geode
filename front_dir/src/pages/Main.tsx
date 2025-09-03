import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { LatLngExpression } from "leaflet";
import { ChevronLeftIcon } from "@heroicons/react/24/outline";
import { isStationFiltered, showModal } from "@utils";
import { useAuth, useApi, useEscape, useLocalStorage } from "@hooks";
import {
    getAffectedStationsService,
    getEarthquakesService,
    getStationsService,
} from "@services";
import {
    Map,
    MapSkeleton,
    SearchInput,
    Sidebar,
    Spinner,
    StationsModal,
    MainScroller,
    EarthQuakeFormModal,
    EarthQuakeScroller,
    DropLeft,
} from "@componentsReact";
import {
    GetParams,
    FilterState,
    StationData,
    StationServiceData,
    StationsAffectedServiceData,
    EarthQuakeFormState,
    EarthquakeData,
    EarthQuakeParams,
} from "@types";

const MainPage = () => {
    //---------------------------------------------------------UseAuth-------------------------------------------------------------
    const { token, logout } = useAuth();

    //---------------------------------------------------------UseApi-------------------------------------------------------------
    const api = useApi(token, logout);

    //---------------------------------------------------------UseLocation-------------------------------------------------------------
    const location = useLocation();

    //---------------------------------------------------------UseLocalStorage-------------------------------------------------------------

    const [, setEarthquakeChosenStorage] = useLocalStorage(
        "earthquakeChosen",
        JSON.stringify({}),
    );

    const [mapStateStorage, setMapStateStorage] = useLocalStorage(
        "mapState",
        "false",
    );

    //---------------------------------------------------------UseRef-------------------------------------------------------------

    const abortControllerRef = useRef<AbortController | null>(null);
    const abortAffectedStationsRef = useRef<AbortController | null>(null);

    //---------------------------------------------------------UseState-------------------------------------------------------------
    const [vectorMagnitude, setVectorMagnitude] = useState<number>(1);

    const [forceSyncScrollerMap, setForceSyncScrollerMap] = useState(0);

    const [earthQuakeAffectedParams, setEarthQuakeAffectedParams] = useState<
        number | undefined
    >(undefined);

    const [earthQuakeAffectedStations, setEarthQuakeAffectedStations] =
        useState<StationsAffectedServiceData | undefined>(undefined);

    const [toggleStateEarthquakeMask, setToggleEarthquakeMask] =
        useState<boolean>(true); // represents coseismic + postseismic

    const [toggleCoseismicVector, setToggleCoseismicVector] =
        useState<boolean>(false); // represents Show co-seismic vectors

    const [chosenEarthquake, setChosenEarthquake] = useState<
        EarthquakeData | undefined
    >(undefined);

    const [posToFly, setPosToFly] = useState<LatLngExpression | undefined>(
        undefined,
    );

    const [earthquakes, setEarthquakes] = useState<
        EarthquakeData[] | undefined
    >(undefined);

    const [station, setStation] = useState<StationData | undefined>(undefined);

    const [stations, setStations] = useState<StationData[] | undefined>(
        undefined,
    );

    const [initialStations, setInitialStations] = useState<
        StationData[] | undefined
    >(undefined);

    const [initialCenter, setInitialCenter] = useState<
        LatLngExpression | undefined
    >(undefined);

    const [formstate, setFormState] = useState<EarthQuakeFormState>({
        date_start: undefined,
        date_end: undefined,
        max_magnitude: "",
        min_magnitude: "",
        id: "",
        max_depth: "",
        min_depth: "",
        min_latitude: "",
        max_latitude: "",
        min_longitude: "",
        max_longitude: "",
        polygon_coordinates: [[]],
    });

    const [markersByBounds, setMarkersByBounds] = useState<
        StationData[] | EarthquakeData[] | undefined
    >(undefined);

    const [mapState, setMapState] = useState<boolean>(
        mapStateStorage === "true" ? true : false,
    );

    const [list, setList] = useState<boolean>(false);

    const [loading, setLoading] = useState<boolean>(true);

    const [spinner, setSpinner] = useState<boolean>(false);

    const [earthquakeSpinner, setEarthquakeSpinner] = useState<boolean>(false);

    const [stationsUpdated, setStationsUpdated] = useState<boolean>(false);

    const [params, setParams] = useState<GetParams>({
        country_code: "",
        network_code: "",
        station_code: "",
        limit: 0,
        offset: 0,
    });

    const [earthquakeModal, setEarthquakeModal] = useState<
        | { show: boolean; title: string; type: "add" | "edit" | "none" }
        | undefined
    >(undefined);

    const [showScroller, setShowScroller] = useState(false);

    const [topoMapState, setTopoMapState] = useState(false);

    const [filterState, setFilterState] = useState<FilterState>({
        statusOption: [],
        typeOption: [],
    });

    const [filters, setFilters] = useState({
        openFilters: false,
        stationType: false,
        stationWithProblems: false,
        stationWithoutProblems: false,
        stationStatus: false,
    });

    const [showEarthQuakesList, setShowEarthQuakesList] =
        useState<boolean>(false);

    const [earthQuakeParams, setEarthQuakeParams] = useState<
        EarthQuakeParams | undefined
    >(undefined);

    const [earthQuakeFiltered, setEarthQuakeFiltered] = useState<
        EarthquakeData[]
    >([]);

    //---------------------------------------------------------Constantes------------------------------------------------------------

    const windowState = window.history?.state?.usr as StationData;

    const locationState =
        location.state && windowState
            ? windowState
            : (location.state as StationData);

    //---------------------------------------------------------Funciones-------------------------------------------------------------

    const getStations = useCallback(async () => {
        setSpinner(true);

        // Cancel the previous request if it exists
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
        }

        // Create a new AbortController for the new request
        const abortController = new AbortController();
        abortControllerRef.current = abortController;

        try {
            const result = await getStationsService<StationServiceData>(
                api,
                params,
                { signal: abortController.signal },
            );
            if (result) {
                setStations(result.data);
                setStationsUpdated(true);
                if (result.data?.length > 0) {
                    result.data.find((s) => {
                        if (s.lat && s.lon) {
                            const hasEqualParams =
                                JSON.stringify(locationState?.mainParams) ===
                                JSON.stringify(params);

                            if (hasEqualParams) {
                                setInitialCenter([
                                    locationState?.lat,
                                    locationState?.lon,
                                ]);
                            } else {
                                setInitialCenter([s.lat, s.lon]);
                            }
                        }
                    });
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setSpinner(false);
        }
    }, [api, params, locationState]);

    const getInitialStations = useCallback(async () => {
        try {
            setLoading(true);
            const result = await getStationsService<StationServiceData>(
                api,
                params,
            );
            if (result) {
                setInitialStations(result.data);
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [api, params]);

    const getAffectedStations = useCallback(async () => {
        setEarthquakeSpinner(true);

        if (abortAffectedStationsRef.current) {
            abortAffectedStationsRef.current.abort();
            if (earthQuakeAffectedParams === undefined) {
                abortAffectedStationsRef.current.abort();
                setEarthquakeSpinner(false);
                return;
            }
        }

        const abortController = new AbortController();
        abortAffectedStationsRef.current = abortController;

        try {
            setEarthQuakeAffectedStations(undefined);

            if (earthQuakeAffectedParams) {
                const result =
                    await getAffectedStationsService<StationsAffectedServiceData>(
                        api,
                        earthQuakeAffectedParams,
                        { signal: abortAffectedStationsRef.current.signal },
                    );
                if (result && result.affected_stations_including_postseismic) {
                    setEarthQuakeAffectedStations(result);
                }
            }
        } catch (err: unknown) {
            console.error(err);
        } finally {
            // Only set spinner to false if the request wasn't aborted
            if (!abortController.signal.aborted) {
                setEarthquakeSpinner(false);
            }
        }
    }, [api, earthQuakeAffectedParams]);

    const getEarthquakes = useCallback(async () => {
        setEarthquakeSpinner(true);

        try {
            const res = await getEarthquakesService<any>(api, earthQuakeParams);

            const filteredEarthquakes =
                (Array.isArray(formstate?.polygon_coordinates) &&
                    formstate.polygon_coordinates[0].length > 0) ||
                !isEmpty(formstate?.max_latitude) ||
                !isEmpty(formstate?.min_latitude) ||
                !isEmpty(formstate?.max_longitude) ||
                !isEmpty(formstate?.min_longitude)
                    ? res.data?.filter((s: EarthquakeData) =>
                          isEarthquakeFiltered(s),
                      )
                    : res.data;

            setEarthquakes(filteredEarthquakes);
        } catch (err) {
            console.error(err);
        } finally {
            !chosenEarthquake ? setEarthquakeSpinner(false) : null;
        }
    }, [api, earthQuakeParams, formstate, chosenEarthquake]);

    const isEmpty = (s: string | undefined) => {
        return s === "" || s === null || s === undefined;
    };

    const isEarthquakeFiltered = (earthquake: EarthquakeData | undefined) => {
        if (earthquake) {
            if (formstate) {
                if (
                    formstate.max_latitude ||
                    formstate.min_latitude ||
                    formstate.max_longitude ||
                    formstate.min_longitude
                ) {
                    return handleLimits(earthquake, formstate);
                }
            } else {
                return false;
            }
        }
    };

    const handleLimits = (
        earthquake: EarthquakeData,
        formState: EarthQuakeFormState,
    ) => {
        const { max_lattitude, min_lattitude, max_longitude, min_longitude } = {
            max_lattitude: Number(formState.max_latitude),
            min_lattitude: Number(formState.min_latitude),
            max_longitude: Number(formState.max_longitude),
            min_longitude: Number(formState.min_longitude),
        };

        if (max_lattitude && min_lattitude && max_longitude && min_longitude) {
            return (
                earthquake.lat <= max_lattitude &&
                earthquake.lat >= min_lattitude &&
                earthquake.lon <= max_longitude &&
                earthquake.lon >= min_longitude
            );
        } else if (max_lattitude && min_lattitude && max_longitude) {
            return (
                earthquake.lat <= max_lattitude &&
                earthquake.lat >= min_lattitude &&
                earthquake.lon <= max_longitude
            );
        } else if (max_lattitude && min_lattitude && min_longitude) {
            return (
                earthquake.lat <= max_lattitude &&
                earthquake.lat >= min_lattitude &&
                earthquake.lon >= min_longitude
            );
        } else if (max_lattitude && max_longitude && min_longitude) {
            return (
                earthquake.lat <= max_lattitude &&
                earthquake.lon <= max_longitude &&
                earthquake.lon >= min_longitude
            );
        } else if (min_lattitude && max_longitude && min_longitude) {
            return (
                earthquake.lat >= min_lattitude &&
                earthquake.lon <= max_longitude &&
                earthquake.lon >= min_longitude
            );
        } else if (max_lattitude && min_lattitude) {
            return (
                earthquake.lat <= max_lattitude &&
                earthquake.lat >= min_lattitude
            );
        } else if (max_longitude && min_longitude) {
            return (
                earthquake.lon <= max_longitude &&
                earthquake.lon >= min_longitude
            );
        } else if (max_lattitude && max_longitude) {
            return (
                earthquake.lat <= max_lattitude &&
                earthquake.lon <= max_longitude
            );
        } else if (max_lattitude && min_longitude) {
            return (
                earthquake.lat <= max_lattitude &&
                earthquake.lon >= min_longitude
            );
        } else if (min_lattitude && max_longitude) {
            return (
                earthquake.lat >= min_lattitude &&
                earthquake.lon <= max_longitude
            );
        } else if (min_lattitude && min_longitude) {
            return (
                earthquake.lat >= min_lattitude &&
                earthquake.lon >= min_longitude
            );
        } else if (max_lattitude) {
            return earthquake.lat <= max_lattitude;
        } else if (min_lattitude) {
            return earthquake.lat >= min_lattitude;
        } else if (max_longitude) {
            return earthquake.lon <= max_longitude;
        } else if (min_longitude) {
            return earthquake.lon >= min_longitude;
        }

        return false;
    };

    const handleEarthQuakeParams = (formstate: EarthQuakeFormState) => {
        setEarthQuakeParams({
            date_start: formstate.date_start ? formstate.date_start : undefined,
            date_end: formstate.date_end ? formstate.date_end : undefined,
            max_magnitude: formstate.max_magnitude
                ? parseFloat(formstate.max_magnitude)
                : undefined,
            min_magnitude: formstate.min_magnitude
                ? parseFloat(formstate.min_magnitude)
                : undefined,
            id: formstate.id ? formstate.id : undefined,
            max_depth: formstate.max_depth
                ? parseFloat(formstate.max_depth)
                : undefined,
            min_depth: formstate.min_depth
                ? parseFloat(formstate.min_depth)
                : undefined,
        });
    };

    const handleEarthquakes = () => {
        setChosenEarthquake(undefined);

        handleEarthQuakeParams(formstate);

        setEarthQuakeAffectedStations(undefined);

        setMapState(true);
    };

    const handleEarthquakeState = (earthquake: EarthquakeData) => {
        if (
            chosenEarthquake?.api_id !== earthquake.api_id ||
            chosenEarthquake === undefined
        ) {
            const storedEarthquakeChosen = JSON.parse(
                localStorage.getItem("earthquakeChosen") ?? "{}",
            );
            setEarthQuakeAffectedParams(earthquake.api_id);

            setChosenEarthquake(earthquake);
            setToggleEarthquakeMask(storedEarthquakeChosen.ui_toggle_mask);
        } else if (chosenEarthquake?.api_id === earthquake.api_id) {
            setChosenEarthquake(undefined);

            localStorage.removeItem("earthquakeChosen");

            setEarthQuakeAffectedParams(undefined);
            setToggleEarthquakeMask(false);
        }
    };

    const stationsByFilters = (stations: StationData[]) => {
        const filteredStations =
            filters?.stationWithProblems ||
            filters?.stationWithoutProblems ||
            (Array.isArray(filterState?.statusOption) &&
                filterState?.statusOption.length > 0) ||
            (Array.isArray(filterState?.typeOption) &&
                filterState?.typeOption.length > 0)
                ? stations?.filter((s) =>
                      isStationFiltered(s, filterState, filters),
                  )
                : stations;

        return filteredStations;
    };

    const exitEarthquakes = () => {
        if (mapState) {
            setMapState(false);
            setShowEarthQuakesList(false);
            setEarthQuakeParams(undefined);
            setEarthQuakeFiltered([]);
            setChosenEarthquake(undefined);
        }
    };

    const handleEarthquakeClose = () => {
        setChosenEarthquake(undefined);
        setMapState(false);
        setShowEarthQuakesList(false);
        setEarthQuakeAffectedParams(undefined);
    };

    //---------------------------------------------------------UseEffect-------------------------------------------------------------
    useEffect(() => {
        if (chosenEarthquake !== undefined) {
            setEarthQuakeAffectedParams(chosenEarthquake.api_id);
            try {
                const stored = localStorage.getItem("earthquakeChosen");
                if (stored) {
                    const parsed = JSON.parse(stored);
                    if (parsed?.api_id === chosenEarthquake.api_id) {
                        const merged = { ...chosenEarthquake, ...parsed };
                        setEarthquakeChosenStorage(JSON.stringify(merged));
                    } else {
                        setEarthquakeChosenStorage(
                            JSON.stringify(chosenEarthquake),
                        );
                    }
                } else {
                    setEarthquakeChosenStorage(
                        JSON.stringify(chosenEarthquake),
                    );
                }
            } catch (err) {
                console.error("Failed to update earthquakeChosen storage", err);
                setEarthquakeChosenStorage(JSON.stringify(chosenEarthquake));
            }
            if (mapState) {
                // setPosToFly([chosenEarthquake.lat, chosenEarthquake.lon]);
            }
        }
        if (chosenEarthquake === undefined && mapState === false) {
            localStorage.removeItem("earthquakeChosen");
        }
        if (earthQuakeAffectedParams === undefined) {
            setEarthQuakeAffectedStations(undefined);
        }
    }, [chosenEarthquake]);

    useEffect(() => {
        earthQuakeAffectedParams && getAffectedStations();
    }, [earthQuakeAffectedParams]);

    useEffect(() => {
        const storedMapState = localStorage.getItem("mapState");
        const storedEarthquakeChosen = localStorage.getItem("earthquakeChosen");
        const storagedFormState = localStorage.getItem("earthQuakeFilters");

        if (storedMapState) {
            setMapState(storedMapState === "true");
        }

        if (storedEarthquakeChosen) {
            setChosenEarthquake(JSON.parse(storedEarthquakeChosen));
        }
        if (storedMapState === "true") {
            setShowEarthQuakesList(true);
            if (storagedFormState) {
                handleEarthQuakeParams(JSON.parse(storagedFormState));
            }
        }
        if (!initialStations) {
            // FIXME: Corresponder las initialstations al rango de la vista
            //que el usuario tenga determinada ??¿¿, seguro tenga que hacer un nuevo getStations
            getInitialStations();
        }
        if (storagedFormState) {
            setFormState(JSON.parse(storagedFormState));
        }
    }, []);

    useEffect(() => {
        if (mapState && earthQuakeParams !== undefined) {
            getEarthquakes();
        }
    }, [earthQuakeParams]);

    useEffect(() => {
        if (earthquakes) {
            setEarthQuakeFiltered(earthquakes);
        }
    }, [earthquakes]);

    useEffect(() => {
        if (
            (initialStations &&
                params.station_code === " " && // PARAMS RESETEADOS
                params.country_code === " " &&
                params.network_code === " ") ||
            (params.station_code === "" && // PARAMS RESETEADOS
                params.country_code === "" &&
                params.network_code === "")
        ) {
            abortControllerRef.current?.abort();
            setStations(initialStations);
            setInitialCenter(
                initialCenter
                    ? [
                          (initialCenter as [number, number])[0],
                          (initialCenter as [number, number])[1],
                      ]
                    : undefined,
            );
        }
        if (
            initialStations &&
            (params.country_code?.trim() !== "" ||
                params.network_code?.trim() !== "" ||
                params.station_code?.trim() !== "")
        ) {
            getStations();
        }
    }, [params]);

    useEffect(() => {
        if (stationsUpdated) {
            if (
                params.station_code === "" && // PARAMS RESETEADOS
                params.country_code === "" &&
                params.network_code === ""
            ) {
                setStations(initialStations);
            }

            setStationsUpdated(false); // Resetear el estado
        }
    }, [stationsUpdated]);

    useEffect(() => {
        const stateCoordinates =
            locationState !== null && Object.values(locationState).length > 0
                ? ([locationState?.lat, locationState?.lon] as LatLngExpression)
                : undefined;

        if (stateCoordinates) {
            setInitialCenter(stateCoordinates);
        }
    }, [locationState]);

    useEffect(() => {
        earthquakeModal?.show && showModal(earthquakeModal.title);
    }, [earthquakeModal]);

    useEffect(() => {
        const earthquake: EarthquakeData | undefined = earthquakes
            ? earthquakes[0]
            : undefined;

        if (
            earthquake &&
            earthquakes?.length === 1 &&
            chosenEarthquake === undefined
        ) {
            setPosToFly([earthquake.lat, earthquake.lon]);
        }
    }, [earthquakes]);

    useEffect(() => {
        setMapStateStorage(mapState.toString());

        //FAL 190825- ACA HABIA UN LOADING PARA ESPERAR LOS ZOOMS EN TIMEOUTS QUE ESTABA EN EL MAP

        // if (!mapState && markersByBounds && markersByBounds.length > 0) {
        //     setLoading2(true);

        //     setTimeout(() => {
        //         setLoading2(false);
        //     }, 2900);
        // }
    }, [mapState]);

    //---------------------------------------------------------UseEscape-------------------------------------------------------------

    useEscape(exitEarthquakes);
    //---------------------------------------------------------Return-------------------------------------------------------------

    return (
        <div
            className={
                "my-auto flex flex-1 transition-all duration-200 relative "
            }
        >
            {loading ? (
                <MapSkeleton
                    styles={{
                        backgroundColor: "rgb(202, 202, 202)",
                        zIndex: 1000000000000000,
                        width: "100vw",
                        position: "absolute",
                        height: "92vh",
                    }}
                />
            ) : (
                <>
                    <MainScroller
                        altData={{
                            dataFiltered: stationsByFilters(stations || []),
                            originalDataCount: stations?.length,
                            hasEarthquakes: showEarthQuakesList,
                        }}
                        mapState={mapState}
                        topoMapState={topoMapState}
                        fromMain={true}
                        filters={filters}
                        filterState={filterState}
                        showScroller={showScroller}
                        setFilters={setFilters}
                        setFormState={setFormState}
                        setFilterState={setFilterState}
                        setTopoMapState={setTopoMapState}
                        setShowScroller={setShowScroller}
                        setShowEarthquakeModal={setEarthquakeModal}
                    />
                    <EarthQuakeScroller
                        forceSyncMapScroller={forceSyncScrollerMap}
                        spinner={earthquakeSpinner}
                        scrollerCondition={showEarthQuakesList}
                        earthquakes={earthquakes || []}
                        earthquakeChosen={chosenEarthquake}
                        handleEarthquakeState={handleEarthquakeState}
                        handleEarthquakeClose={handleEarthquakeClose}
                        earthquakeAffectedStations={earthQuakeAffectedStations}
                        setToggleEarthquakeMask={setToggleEarthquakeMask}
                        setToggleCoseismicVector={setToggleCoseismicVector}
                        vectorMagnitude={vectorMagnitude}
                        setVectorMagnitude={setVectorMagnitude}
                    />
                    {earthquakeModal &&
                        earthquakeModal.title === "earthquake" && (
                            <EarthQuakeFormModal
                                formstate={formstate}
                                handleEarthquakes={handleEarthquakes}
                                setInitialCenter={setInitialCenter}
                                setShowEarthquakeModal={setEarthquakeModal}
                                setFormState={setFormState}
                                setShowEarthQuakesList={setShowEarthQuakesList}
                                setPosToFly={setPosToFly}
                            />
                        )}
                    {!mapState ? <Sidebar station={station} /> : null}
                    <div
                        className={"self-center w-full flex flex-col flex-wrap"}
                    >
                        <div
                            className={`absolute right-0 z-50 h-4/6 flex items-center`}
                        >
                            {" "}
                            {!mapState && (
                                <button
                                    className="btn top-1/2 right-0 absolute"
                                    style={{
                                        writingMode: "vertical-rl",
                                        width: "50px",
                                        height: "200px",
                                        fontSize: "18px",
                                    }}
                                    onClick={() => {
                                        setList(!list);
                                    }}
                                >
                                    {" "}
                                    Station lists
                                    <ChevronLeftIcon className="h-6 w-6" />
                                </button>
                            )}
                        </div>
                        <div
                            id="search-input"
                            className="flex justify-center flex-wrap items-center absolute z-[12] w-full top-8"
                        >
                            {!mapState && (
                                <SearchInput
                                    params={params}
                                    stations={stations}
                                    setParams={setParams}
                                    setStation={setStation}
                                />
                            )}
                        </div>
                        {spinner && (
                            <div className="absolute mt-28 right-52 z-50">
                                {" "}
                                <Spinner size={"lg"} />
                            </div>
                        )}
                        {mapState && (
                            <DropLeft
                                mapState={showEarthQuakesList}
                                setShowEarthquakeList={setShowEarthQuakesList}
                            />
                        )}
                        <Map
                            initialCenter={initialCenter}
                            topoMap={topoMapState}
                            posToFly={posToFly}
                            handleEarthquakeState={handleEarthquakeState}
                            mapState={mapState}
                            mainParams={params}
                            markersByBounds={markersByBounds}
                            filters={filters}
                            filterState={filterState}
                            forceSyncScrollerMap={forceSyncScrollerMap}
                            earthquakes={earthquakes ? earthquakes : []}
                            earthQuakeChosen={chosenEarthquake}
                            earthquakesFiltered={earthQuakeFiltered || []}
                            earthquakeAffectedStations={
                                earthQuakeAffectedStations
                            }
                            setShowScroller={setShowScroller}
                            stations={stations ? stations : initialStations}
                            showEarthquakeList={showEarthQuakesList}
                            setMarkersByBounds={setMarkersByBounds}
                            setEarthquakesFiltered={setEarthQuakeFiltered}
                            setForceSyncScrollerMap={setForceSyncScrollerMap}
                            toggleStateEarthquakeMask={
                                toggleStateEarthquakeMask
                            }
                            toggleCoseismicVector={toggleCoseismicVector}
                            vectorMagnitude={vectorMagnitude}
                            setVectorMagnitude={setVectorMagnitude}
                        />

                        {list && (
                            <div
                                className={`fixed right-20   
                                transition-all mt-[88px] h-fit flex z-50 `}
                            >
                                <StationsModal
                                    setState={setList}
                                    stations={stations}
                                    mainParams={params}
                                />
                            </div>
                        )}
                    </div>
                </>
            )}
        </div>
    );
};

export default MainPage;

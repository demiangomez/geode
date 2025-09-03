import L, { latLng, LatLngExpression, MarkerCluster } from "leaflet";

import * as toGeoJSON from "@tmcw/togeojson";
import JSZip from "jszip";
import MarkerClusterGroup from "react-leaflet-markercluster";

import {
    MapContainer,
    Marker,
    Popup,
    ScaleControl,
    TileLayer,
    Tooltip,
    useMap,
    ZoomControl,
} from "react-leaflet";

import { Fragment, useEffect, useMemo, useState } from "react";

import { MapVector, PopupChildren, Slider } from "@componentsReact";

import { useLocalStorage, useAuth, useApi } from "@hooks";

import {
    EarthquakeData,
    FilterState,
    GetParams,
    MyMapContainerProps,
    StationData,
    StationsAffectedServiceData,
    StationAffectedInfo,
    StationTypeServiceData,
    StationStatusServiceData,
    StationStatusData,
    StationTypeData,
} from "@types";

import { isStationFiltered, chosenIcon } from "@utils";
import { getStationTypesService, getStationStatusService } from "@services";

interface MapProps {
    handleEarthquakeState: (earthquake: EarthquakeData) => void;
    initialCenter: LatLngExpression | undefined;
    posToFly: LatLngExpression | undefined;
    topoMap?: boolean | undefined;
    filters?: {
        openFilters: boolean;
        stationType: boolean;
        stationWithProblems: boolean;
        stationWithoutProblems: boolean;
        stationStatus: boolean;
    };
    filterState?: FilterState;
    forceSyncScrollerMap?: number;
    mapState: boolean;
    mainParams: GetParams;
    markersByBounds?: StationData[] | EarthquakeData[];
    earthquakes: EarthquakeData[];
    earthquakesFiltered: EarthquakeData[];
    earthquakeAffectedStations: StationsAffectedServiceData | undefined;
    earthQuakeChosen: EarthquakeData | undefined;
    toggleStateEarthquakeMask: boolean;
    toggleCoseismicVector: boolean;
    stations: StationData[] | undefined;
    showEarthquakeList: boolean;
    setForceSyncScrollerMap: React.Dispatch<React.SetStateAction<number>>;
    setEarthquakesFiltered: React.Dispatch<
        React.SetStateAction<EarthquakeData[]>
    >;
    setMarkersByBounds: React.Dispatch<
        React.SetStateAction<StationData[] | EarthquakeData[] | undefined>
    >;
    setMainParams?: React.Dispatch<React.SetStateAction<GetParams>>;
    setShowScroller: React.Dispatch<React.SetStateAction<boolean>>;
    vectorMagnitude: number;
    setVectorMagnitude: React.Dispatch<React.SetStateAction<number>>;
}

export const ChangeView = ({
    center,
    zoom,
}: {
    center: LatLngExpression;
    zoom: number;
}) => {
    const map = useMap();

    useEffect(() => {
        map.setView(center, zoom);
    }, [center, zoom, map]);

    return null;
};

//Component for adding kml render to leaflet map from kml in base64 format
// En el componente LoadKmzFromBase64

// Corrige primero la definición del componente:
const LoadKmzFromBase64 = ({
    base64Data,
    shouldFitBounds = false,
    earthQuakeChosen,
}: {
    base64Data: string;
    shouldFitBounds?: boolean;
    earthQuakeChosen?: EarthquakeData;
}) => {
    const map = useMap();

    const removeOldKmls = () => {
        map.eachLayer((layer) => {
            if (
                !(layer instanceof L.TileLayer) &&
                !(layer instanceof L.Marker)
                // && !(layer instanceof L.FeatureGroup) //no Remueve instancias de los cluster
            ) {
                map.removeLayer(layer);
            }
        });
    };

    useEffect(() => {
        const loadKmzOrKmlFile = async () => {
            removeOldKmls();
            if (!base64Data) return;

            // Guarda el estado actual de la vista
            const savedPosition = localStorage.getItem("lastPosition");

            const finalPosition = savedPosition
                ?.split(",")
                .map((pos) => parseFloat(pos)) as [number, number];

            const currentCenter = latLng(...finalPosition) || map.getCenter();
            const currentZoom =
                Number(localStorage.getItem("lastZoomLevel")) || map.getZoom();

            try {
                // Decodificar base64
                const binaryString = atob(base64Data);
                const len = binaryString.length;
                const bytes = new Uint8Array(len);
                for (let i = 0; i < len; i++) {
                    bytes[i] = binaryString.charCodeAt(i);
                }
                const arrayBuffer = bytes.buffer;

                const parseAndAddGeoJSON = (kmlString: string) => {
                    const dom = new DOMParser().parseFromString(
                        kmlString,
                        "application/xml",
                    );

                    const geojson = toGeoJSON.kml(dom);
                    const geoJsonLayer = L.geoJSON(geojson, {
                        style: (feature) => {
                            return feature
                                ? {
                                      color: feature.properties.stroke,
                                      opacity:
                                          feature.properties["stroke-opacity"],
                                      fillColor:
                                          feature.properties["fill-color"],
                                  }
                                : {};
                        },
                        pointToLayer: (feature, latlng) => {
                            // if (feature.properties && shouldFitBounds) {
                            const iconUrl =
                                feature.properties.icon ||
                                "https://maps.google.com/mapfiles/kml/shapes/star.png";
                            const iconScale =
                                feature.properties["icon-scale"] || 1;
                            const iconOpacity =
                                feature.properties["icon-opacity"] || 1;

                            const baseSize = 32;
                            const scaledSize = Math.round(baseSize * iconScale);

                            const customIcon = L.icon({
                                iconUrl: iconUrl,
                                iconSize: [scaledSize, scaledSize],
                                iconAnchor: [scaledSize / 2, scaledSize / 2],
                                className: "light-red-icon",
                            });

                            const marker = L.marker(latlng, {
                                icon: customIcon,
                                opacity: iconOpacity,
                            });

                            marker.bindPopup(feature.properties.description);
                            if (shouldFitBounds) {
                                setTimeout(() => marker.openPopup(), 100);
                            }

                            return marker;
                            // } else {
                            //     return L.marker(latlng);
                            // }
                        },
                    });

                    // Añade la capa al mapa primero
                    geoJsonLayer.addTo(map);

                    // Centra el mapa en el terremoto si es la primera carga
                    if (shouldFitBounds) {
                        if (earthQuakeChosen?.lat && earthQuakeChosen?.lon) {
                            try {
                                const bounds = geoJsonLayer.getBounds();
                                if (bounds.isValid()) {
                                    setTimeout(() => {
                                        map.fitBounds(bounds, {
                                            padding: [50, 50],
                                            maxZoom: 8,
                                            animate: false,
                                        });
                                        // Solo si los bounds están muy lejos, centra en el epicentro
                                        const center = bounds.getCenter();
                                        const distanceFromEQ = map.distance(
                                            [
                                                earthQuakeChosen.lat,
                                                earthQuakeChosen.lon,
                                            ],
                                            [center.lat, center.lng],
                                        );
                                        if (distanceFromEQ > 500000) {
                                            map.setView(
                                                [
                                                    earthQuakeChosen.lat,
                                                    earthQuakeChosen.lon,
                                                ],
                                                8,
                                            );
                                        }
                                    }, 300);
                                } else {
                                    // Solo como fallback si no hay bounds válidos
                                    map.setView(
                                        [
                                            earthQuakeChosen.lat,
                                            earthQuakeChosen.lon,
                                        ],
                                        currentZoom,
                                    );
                                }
                            } catch (error) {
                                console.error(
                                    "Error al ajustar bounds:",
                                    error,
                                );
                                map.setView(
                                    [
                                        earthQuakeChosen.lat,
                                        earthQuakeChosen.lon,
                                    ],
                                    currentZoom,
                                );
                            }
                        }
                    } else {
                        map.setView(currentCenter, currentZoom);
                    }
                };

                // Procesar KMZ/KML
                try {
                    // Intenta como KMZ
                    const zip = await JSZip.loadAsync(arrayBuffer);
                    const kmlFile = zip.file(/.*\.kml/)[0];

                    if (kmlFile) {
                        const kmlString = await kmlFile.async("string");
                        parseAndAddGeoJSON(kmlString);
                    } else {
                        console.error("No KML file found in KMZ.");
                    }
                } catch (kmzError) {
                    try {
                        // Intenta como KML suelto
                        const kmlString = new TextDecoder().decode(arrayBuffer);
                        parseAndAddGeoJSON(kmlString);
                    } catch (kmlError) {
                        console.error("Error parsing KML:", kmlError);
                    }
                }
            } catch (error) {
                console.error("Error decoding base64 file:", error);

                // Fallback a coordenadas del terremoto en caso de error
                if (!shouldFitBounds) {
                    map.setView(currentCenter, currentZoom);
                }
            }
        };

        loadKmzOrKmlFile();
    }, [base64Data, map, earthQuakeChosen]);

    return null;
};

const MapMarkers = ({
    stations,
    initialCenter,
    mainParams,
    mapState,
    markersByBounds,
    earthquakes,
    earthquakeAffectedStations,
    earthQuakeChosen,
    earthquakesFiltered,
    toggleStateEarthquakeMask,
    posToFly,
    filters,
    filterState,
    setMarkersByBounds,
    setEarthquakesFiltered,
    handleEarthquakeState,
    setForceSyncScrollerMap,
    toggleCoseismicVector,
    vectorMagnitude,
    setVectorMagnitude,
}: MapProps) => {
    //---------------------------------------------------------UseAuth-------------------------------------------------------------
    const { token, logout, user } = useAuth();

    //---------------------------------------------------------UseApi-------------------------------------------------------------
    const api = useApi(token, logout);

    //---------------------------------------------------------------------------Constantes--------------------------------------------------------------------------------------
    const map = useMap();

    //---------------------------------------------------------------------------UseLocalStorage--------------------------------------------------------------------------------------
    const [lastZoomLevel, setLastZoomLevel] = useLocalStorage(
        "lastZoomLevel",
        "8",
    );

    const [, setLastPosition] = useLocalStorage("lastPosition", "[0,0]");

    const [, setLastMagnitudeVector] = useLocalStorage(
        "lastMagnitudeVector",
        "1",
    );

    //-------------------------------------------------------------------------------UseStates--------------------------------------------------------------------------------------
    const [forceRenderMarker, setForceRenderMarker] = useState(0);

    const [overlappedStations, setOverlappedStations] = useState<
        StationData[] | undefined
    >(undefined);

    const [stationsWithOverlap, setStationsWithOverlap] = useState<
        StationData[] | undefined
    >(undefined);

    const [overlappedClusters, setOverlappedClusters] = useState<
        StationData[][] | undefined
    >(undefined);

    const [isDangerousPopup, setIsDaangerousPopup] = useState<boolean>(false);

    const [types, setTypes] = useState<{ image: string; name: string }[]>([]);

    const [statuses, setStatuses] = useState<{ name: string; color: string }[]>(
        [],
    );

    const [isFirstKmlLoad, setIsFirstKmlLoad] = useState<boolean>(false);

    //-------------------------------------------------------------------------------UseEffects--------------------------------------------------------------------------------------
    //Modify setview when refreshing the page
    useEffect(() => {
        if (posToFly) {
            map.setView(
                posToFly,
                Number(localStorage.getItem("lastZoomLevel")) ??
                    map.getMinZoom(),
            );
        }
    }, [posToFly]);

    useEffect(() => {
        if (!mapState) {
            updateMarkersByBounds();
            // updateScale();
        }
    }, [filters, filterState, mapState]);

    //Used for re rendering well when coming back to station mode from earthquake mode
    // POSIBLE RAZON DEL BUG DEL ISSUE #227
    // useEffect(() => {
    //     if (!mapState && markersByBounds && markersByBounds.length > 0) {
    //         const pos = map.getCenter();
    //         const zoom = map.getZoom();
    //         map.setZoom(map.getMinZoom());
    //         setTimeout(() => {
    //             map.setZoom(6);
    //         }, 500);
    //         setTimeout(() => {
    //             map.setZoom(10);
    //         }, 1200);
    //         setTimeout(() => {
    //             map.setZoom(4);
    //         }, 1800);
    //         setTimeout(() => {
    //             map.setView(pos);
    //             map.setZoom(zoom);
    //         }, 2900);
    //     }
    // }, [mapState]);

    // Updates markers when map moves
    useEffect(() => {
        const onMove = () => {
            if (!mapState) {
                updateMarkersByBounds();
            }
            if (mapState && earthQuakeChosen === undefined)
                updateEarthquakeMarkers();
        };
        map.on("move", onMove);

        return () => {
            map.off("move", onMove);
        };
    }, [
        stations,
        earthquakes,
        map,
        filters,
        filterState,
        mapState,
        earthQuakeChosen,
    ]);

    //Updates storage when you modify zoom and position
    //set map on pan when dragging
    useEffect(() => {
        const southWest = L.latLng(-85.0511, -180);
        const northEast = L.latLng(85.0511, 180);

        const bounds = L.latLngBounds(southWest, northEast);

        const onZoomEnd = () => {
            const currentZoom = map.getZoom();
            setLastZoomLevel(currentZoom.toString());
        };

        const onMoveEnd = () => {
            const currentCenter = map.getCenter();
            setLastPosition([currentCenter.lat, currentCenter.lng].toString());
        };

        map.setMaxBounds(bounds);

        map.on("zoomend", onZoomEnd);
        map.on("moveend", onMoveEnd);

        return () => {
            map.off("zoomend", onZoomEnd);
            map.off("moveend", onMoveEnd);
        };
    }, [map]);

    // Update markers when initialcenter changes
    useEffect(() => {
        if (initialCenter) {
            if (!mapState) {
                map.setView(
                    initialCenter,
                    lastZoomLevel ? parseInt(lastZoomLevel) : 8,
                );
                updateMarkersByBounds();
            }
        }
    }, [initialCenter, map]);

    // Forces map and marker renders when you get earthquake affected stations
    useEffect(() => {
        if (!earthquakeAffectedStations) {
            removeOldKmls();
        }
        if (earthquakes.length > 0) {
            setForceRenderMarker((prev) => prev + 1);
        }
    }, [earthquakeAffectedStations]);

    useEffect(() => {
        // Force re-render markers but don't reset isFirstKmlLoad
        setForceRenderMarker((prev) => prev + 1);
    }, [toggleStateEarthquakeMask]);

    useEffect(() => {
        removeOldKmls();
        setForceRenderMarker((prev) => prev + 1);
        // removeAllStations();
    }, [earthQuakeChosen]);

    useEffect(() => {
        getStationStatuses();
        getStationTypes();
        overlappedStationsByScale();
        updateMarkersByBounds();
    }, []);

    useEffect(() => {
        overlappedStationsByScale();
    }, [user, toggleStateEarthquakeMask, earthquakeAffectedStations]);

    useEffect(() => {
        if (
            Array.isArray(overlappedStations) &&
            overlappedStations.length > 0
        ) {
            const stationsWithOverlapArray = [
                ...new Set(overlappedStations.map((station) => station.api_id)),
            ]
                .map((id) =>
                    overlappedStations.find((station) => station.api_id === id),
                )
                .filter((station) => station !== undefined);
            setStationsWithOverlap(stationsWithOverlapArray);
            let auxExistsSimilar = false;
            const clusters: StationData[][] = [];
            for (let i = 0; i < overlappedStations.length; i += 1) {
                auxExistsSimilar = false;
                const auxClusterIndexes: number[] = overlappedStations
                    .map((station, index) =>
                        station.api_id === overlappedStations[i].api_id
                            ? index
                            : -1,
                    )
                    .filter((index) => index !== -1);
                const auxCluster: StationData[] = auxClusterIndexes.map(
                    (index) =>
                        index % 2 === 0
                            ? overlappedStations[index + 1]
                            : overlappedStations[index - 1],
                );
                auxCluster.push(overlappedStations[i]);

                if (clusters.length > 0) {
                    for (let j = 0; j < clusters.length; j++) {
                        const existSimilar = clusters[j].some((station) =>
                            auxCluster.some(
                                (auxStation) =>
                                    station.api_id === auxStation.api_id,
                            ),
                        );
                        if (existSimilar) {
                            auxExistsSimilar = true;
                            const similarClusterDifferences = auxCluster.filter(
                                (auxStation) =>
                                    !clusters[j].some(
                                        (station) =>
                                            station.api_id ===
                                            auxStation.api_id,
                                    ),
                            );
                            if (similarClusterDifferences.length > 0) {
                                clusters[j].push(...similarClusterDifferences);
                            }
                            break;
                        }
                    }
                    if (auxExistsSimilar === false) {
                        clusters.push(auxCluster);
                    }
                    continue;
                }
                clusters.push(auxCluster);
            }
            setOverlappedClusters(clusters);
        }
    }, [overlappedStations]);

    useEffect(() => {
        if (earthQuakeChosen) {
            const storageEarthquake: EarthquakeData = JSON.parse(
                localStorage.getItem("earthquakeChosen") || "{}",
            );

            storageEarthquake.api_id !== earthQuakeChosen.api_id &&
                setIsFirstKmlLoad(true);
        }
    }, [earthQuakeChosen]);

    useEffect(() => {
        if (earthQuakeChosen && earthquakeAffectedStations && isFirstKmlLoad) {
            setIsFirstKmlLoad(false);
        }
    }, [earthquakeAffectedStations, earthQuakeChosen, isFirstKmlLoad]);

    //-------------------------------------------------------------------------------Funciones--------------------------------------------------------------------------------------
    const areStationsOverlapped = (
        stationA: StationData,
        stationB: StationData,
    ) => {
        const zoom = 16; // map.getZoom() o 10
        const center = map.getCenter();
        const latitude = center.lat;
        const metersPerPixel =
            (156543.03392 * Math.cos((latitude * Math.PI) / 180)) /
            Math.pow(2, zoom);
        const scale = metersPerPixel * 96 * 39.37; // Convert to scale considering screen DPI
        const VISUAL_ACUITY_MM = 3; // Increased from 0.2mm due to larger marker size
        const minDistanceMeters =
            Number(user?.clustering_distance) >= 0
                ? Number(user?.clustering_distance)
                : (VISUAL_ACUITY_MM / 1000) * scale;

        if (
            !stationA?.lat ||
            !stationA?.lon ||
            !stationB?.lat ||
            !stationB?.lon
        ) {
            return false;
        } else {
            const distance = map.distance(
                [stationA.lat, stationA.lon],
                [stationB.lat, stationB.lon],
            );

            if (distance < minDistanceMeters) {
                return true;
            }
        }
    };

    const getAffectedStations = () => {
        if (!earthquakeAffectedStations && earthQuakeChosen) return [];
        const affectedList = toggleStateEarthquakeMask
            ? earthquakeAffectedStations?.affected_stations_including_postseismic
            : earthquakeAffectedStations?.affected_stations_without_postseismic;

        // Devuelve los objetos StationData de las estaciones afectadas
        return (
            stations?.filter((station) =>
                affectedList?.some(
                    (affected) =>
                        affected.network_code === station.network_code &&
                        affected.station_code === station.station_code,
                ),
            ) ?? []
        );
    };

    const overlappedStationsByScale = () => {
        const affectedStations = getAffectedStations();

        if (mapState && (!affectedStations || affectedStations.length === 0))
            return;

        if (!stations || stations.length === 0) return;

        // Use spatial binning to reduce number of comparisons
        const spatialBins: Record<string, StationData[]> = {};
        const binSize = 0.01; // Adjust bin size based on your data distribution

        // Assign stations to spatial bins
        (mapState
            ? earthQuakeChosen
                ? affectedStations
                : []
            : stations
        ).forEach((station) => {
            if (!station.lat || !station.lon) return;

            // Create bin keys based on coordinates rounded to binSize
            const binX = Math.floor(station.lat / binSize);
            const binY = Math.floor(station.lon / binSize);
            const binKey = `${binX}:${binY}`;

            // Add station to its bin and initialize if needed
            if (!spatialBins[binKey]) spatialBins[binKey] = [];
            spatialBins[binKey].push(station);
        });

        // Process each bin and neighboring bins
        const overlappingPairs: StationData[] = [];

        Object.entries(spatialBins).forEach(([binKey, binStations]) => {
            // Check stations within the same bin
            for (let i = 0; i < binStations.length; i++) {
                for (let j = i + 1; j < binStations.length; j++) {
                    if (areStationsOverlapped(binStations[i], binStations[j])) {
                        overlappingPairs.push(binStations[i], binStations[j]);
                    }
                }
            }

            // Get bin coordinates
            const [binX, binY] = binKey.split(":").map(Number);

            // Check neighboring bins (8-connected)
            for (let dx = -1; dx <= 1; dx++) {
                for (let dy = -1; dy <= 1; dy++) {
                    if (dx === 0 && dy === 0) continue; // Skip current bin

                    const neighborKey = `${binX + dx}:${binY + dy}`;
                    const neighborBin = spatialBins[neighborKey];

                    if (neighborBin) {
                        // Check stations between current bin and neighbor bin
                        for (const stationA of binStations) {
                            for (const stationB of neighborBin) {
                                if (areStationsOverlapped(stationA, stationB)) {
                                    overlappingPairs.push(stationA, stationB);
                                }
                            }
                        }
                    }
                }
            }
        });

        // Update state only once with all found pairs
        if (overlappingPairs.length > 0) {
            setOverlappedStations(overlappingPairs);
        }
    };

    //Update markers considering map bounds in viewport
    const updateMarkersByBounds = () => {
        const mapBounds = map.getBounds();
        const mapEastCorner = mapBounds.getNorthEast();
        const mapWestCorner = mapBounds.getSouthWest();

        const filtered = stations?.filter(
            (s) =>
                s?.lat < mapEastCorner?.lat &&
                s?.lon < mapEastCorner?.lng &&
                s?.lat > mapWestCorner?.lat &&
                s?.lon > mapWestCorner?.lng,
        );

        const filteredMarkers =
            filters?.stationWithProblems ||
            filters?.stationWithoutProblems ||
            (Array.isArray(filterState?.statusOption) &&
                filterState?.statusOption.length > 0) ||
            (Array.isArray(filterState?.typeOption) &&
                filterState?.typeOption.length > 0)
                ? filtered?.filter((s) =>
                      isStationFiltered(s, filterState, filters),
                  )
                : filtered;

        if (map.getZoom() >= 10 && mainParams?.station_code === "") {
            const nonOverlappedMarkers = filteredMarkers?.filter(
                (station) =>
                    !stationsWithOverlap?.some(
                        (overlappedStation) =>
                            overlappedStation.api_id === station.api_id,
                    ),
            );
            setMarkersByBounds(nonOverlappedMarkers ?? []);
        } else {
            setMarkersByBounds(filteredMarkers);
        }
    };

    const findStation = (s: StationAffectedInfo) => {
        return stations?.find(
            (station) =>
                station.network_code === s.network_code &&
                station.station_code === s.station_code,
        );
    };

    const updateEarthquakeMarkers = () => {
        const filtered = earthquakes;

        setEarthquakesFiltered(filtered);
    };

    const earthquakeIcon =
        "https://maps.google.com/mapfiles/kml/shapes/star.png";

    const stationTooltip = (s: StationData) => {
        return (s.network_code?.toUpperCase() +
            "." +
            s.station_code?.toUpperCase()) as string;
    };

    const earthquakeToolTip = (s: EarthquakeData) => {
        return `${s.location?.toUpperCase()} (M ${s.mag?.toString()}) ${new Date(s.date).toLocaleDateString()}` as string;
    };

    //choses what markers show on the map according to the map state
    const chosenToMap = () => {
        if (mapState) {
            return earthquakesFiltered as EarthquakeData[];
        } else {
            return markersByBounds as StationData[];
        }
    };

    const removeOldKmls = () => {
        map.eachLayer((layer) => {
            // Remove all layers except TileLayer and Markers
            if (
                !(layer instanceof L.TileLayer) &&
                !(layer instanceof L.Marker)
            ) {
                map.removeLayer(layer);
            }
        });
    };

    const getStationStatuses = async () => {
        try {
            const res =
                await getStationStatusService<StationStatusServiceData>(api);
            if (res) {
                const statuses = res.data.map((status: StationStatusData) => {
                    return {
                        color: status.color_name,
                        name: status.name,
                    };
                });
                setStatuses(statuses);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const getStationTypes = async () => {
        try {
            const res =
                await getStationTypesService<StationTypeServiceData>(api);
            if (res) {
                const types = res.data.map((type: StationTypeData) => {
                    return {
                        image: type.actual_image as string,
                        name: type.name,
                    };
                });
                setTypes(types);
            }
        } catch (err) {
            console.error(err);
        }
    };

    // const removeAllStations = () => {
    //     map.eachLayer((layer) => {
    //         if (layer instanceof L.Marker) {
    //             map.removeLayer(layer);
    //         }
    //     });
    // };

    const isNearTop = (station: StationData) => {
        const result = station.lat;
        if (85.0511 - 3.8 < result) {
            map.setView([station.lat, station.lon], 10);
            setIsDaangerousPopup(true);
        }
    };

    const createClusterWithProblem = function (cluster: MarkerCluster) {
        return L.divIcon({
            html: `<span>${cluster.getChildCount()}</span>`,
            className: "custom-marker-cluster-dangerous",
            iconSize: L.point(30, 30, true),
        });
    };

    const createClusterWithNoProblem = function (cluster: MarkerCluster) {
        return L.divIcon({
            html: `<span>${cluster.getChildCount()}</span>`,
            className: "custom-marker-cluster-normal",
            iconSize: L.point(30, 30, true),
        });
    };

    const anyHasProblems = (stations: StationData[]) => {
        return stations.some((station) => {
            return station.has_gaps || !station.has_stationinfo;
        });
    };

    // Versión optimizada de adjustPopupPosition
    const adjustPopupPosition = (popup: L.Popup) => {
        // Programa el ajuste para el próximo frame usando requestAnimationFrame
        requestAnimationFrame(() => {
            const map = (popup as any)._map as L.Map;
            if (!map) return;

            const latLng = popup.getLatLng();
            if (!latLng) return;

            const popupPos = map.latLngToContainerPoint(latLng);
            const topOffset = 570; // ESTIMATED HEIGHT

            if (popupPos.y < topOffset) {
                const panDistance = topOffset - popupPos.y;

                let adjustedOffset: number;

                if (panDistance > 400) {
                    adjustedOffset = panDistance + 300;
                } else if (panDistance > 300) {
                    adjustedOffset = panDistance + 200;
                } else if (panDistance > 200) {
                    adjustedOffset = panDistance + 170;
                } else if (panDistance > 100) {
                    adjustedOffset = panDistance + 100;
                } else if (panDistance > 60) {
                    adjustedOffset = panDistance + 50;
                } else {
                    return;
                }

                // Limita el ajuste máximo
                adjustedOffset = Math.min(adjustedOffset, 600);

                // PAN MAP DOWN - Usa valores calculados directamente
                map.panBy([0, -adjustedOffset]);
            }
        });
    };

    useEffect(() => {
        if (isDangerousPopup) {
            map.setMinZoom(10);
        } else if (!isDangerousPopup) {
            map.setMinZoom(3);
        }
    }, [isDangerousPopup]);

    const markerClusters = useMemo(() => {
        if (!map || !overlappedClusters) return null;
        return overlappedClusters.map((cluster, index) => {
            return (
                <MarkerClusterGroup
                    key={`cluster-${index}`}
                    iconCreateFunction={
                        anyHasProblems(cluster)
                            ? createClusterWithProblem
                            : createClusterWithNoProblem
                    }
                >
                    {cluster.map((s) => {
                        const iconGaps = chosenIcon(
                            s as StationData,
                            types,
                            statuses,
                        );
                        const pos: LatLngExpression = [s.lat, s.lon];
                        return (
                            <Marker
                                icon={iconGaps}
                                key={s.api_id}
                                position={pos}
                                eventHandlers={{
                                    click: () => {
                                        isNearTop(s as StationData);
                                    },
                                    popupclose: () => {
                                        setIsDaangerousPopup(false);
                                    },
                                }}
                            >
                                <Tooltip>
                                    <strong className="text-lg">
                                        {stationTooltip(s as StationData)}
                                    </strong>
                                </Tooltip>
                                <Popup
                                    maxWidth={600}
                                    minWidth={400}
                                    eventHandlers={{}}
                                >
                                    <PopupChildren
                                        station={s as StationData}
                                        fromMain={true}
                                        mainParams={mainParams}
                                    />
                                </Popup>
                            </Marker>
                        );
                    })}
                </MarkerClusterGroup>
            );
        });
    }, [overlappedClusters, map, types, statuses]);

    return (
        <>
            {mapState &&
            earthquakeAffectedStations !== undefined &&
            earthQuakeChosen !== undefined ? (
                <LoadKmzFromBase64
                    base64Data={
                        toggleStateEarthquakeMask
                            ? earthquakeAffectedStations.kml_including_postseismic
                            : earthquakeAffectedStations.kml_without_postseismic
                    }
                    shouldFitBounds={isFirstKmlLoad}
                    earthQuakeChosen={earthQuakeChosen}
                />
            ) : null}
            {mapState &&
                earthquakeAffectedStations &&
                (toggleStateEarthquakeMask
                    ? earthquakeAffectedStations?.affected_stations_including_postseismic
                    : earthquakeAffectedStations?.affected_stations_without_postseismic
                )?.map((s: StationAffectedInfo, index: number) => {
                    const station = findStation(s);

                    const displacement =
                        earthquakeAffectedStations.coseismic_displacements.find(
                            (d) =>
                                d.NetworkCode === s.network_code &&
                                d.StationCode === s.station_code,
                        );

                    const uniqueKey = `affected-${station?.network_code}-${station?.station_code}-${index}-${forceRenderMarker}`;
                    return station && station.lat && station.lon ? (
                        <Fragment key={uniqueKey}>
                            <Marker
                                icon={chosenIcon(
                                    station as StationData,
                                    types,
                                    statuses,
                                )}
                                key={uniqueKey + forceRenderMarker}
                                position={[station.lat, station.lon]}
                                eventHandlers={{
                                    click: () => {
                                        isNearTop(station as StationData);
                                    },
                                    popupclose: () => {
                                        setIsDaangerousPopup(false);
                                    },
                                }}
                            >
                                <Tooltip permanent={false}>
                                    <strong className="text-lg">
                                        {stationTooltip(station as StationData)}
                                    </strong>
                                </Tooltip>
                                <Popup maxWidth={600} minWidth={400}>
                                    <PopupChildren
                                        station={station as StationData}
                                        fromMain={true}
                                        mainParams={mainParams}
                                    />
                                </Popup>
                            </Marker>
                            {toggleCoseismicVector &&
                            earthQuakeChosen !== undefined &&
                            displacement ? (
                                <MapVector
                                    key={`vector-${uniqueKey}`}
                                    origin={station}
                                    displacement={{
                                        north: displacement.n,
                                        east: displacement.e,
                                    }}
                                    magnitude={vectorMagnitude ?? 1}
                                />
                            ) : null}
                        </Fragment>
                    ) : null;
                })}

            {map.getZoom() >= 10 &&
                mainParams?.station_code === "" &&
                overlappedClusters &&
                overlappedClusters.length > 0 &&
                markerClusters}
            {markersByBounds &&
                chosenToMap()
                    .filter((s) => s?.lat != null && s?.lon != null)
                    .map((s: StationData | EarthquakeData, index: number) => {
                        const pos: LatLngExpression = [s.lat, s.lon];
                        const size: [number, number] =
                            s?.api_id === earthQuakeChosen?.api_id
                                ? [40, 40]
                                : [30, 30];
                        const color =
                            s?.api_id === earthQuakeChosen?.api_id
                                ? "light-red-icon"
                                : "yellow-icon";
                        const uniqueKey = `${s?.lat}-${s?.lon}-${s?.api_id ?? index}`;
                        if (mapState) {
                            if (
                                earthQuakeChosen === undefined ||
                                earthquakeAffectedStations === undefined
                            ) {
                                return (
                                    <Marker
                                        icon={
                                            new L.Icon({
                                                iconUrl: earthquakeIcon,
                                                className: color,
                                                iconSize: size,
                                            })
                                        }
                                        eventHandlers={{
                                            click: () => {
                                                handleEarthquakeState(
                                                    s as EarthquakeData,
                                                );

                                                setForceSyncScrollerMap(
                                                    (prev) => prev + 1,
                                                );
                                                setForceRenderMarker(
                                                    (prev) => prev + 1,
                                                );
                                                isNearTop(s as StationData);
                                            },
                                            popupclose: () => {
                                                setIsDaangerousPopup(false);
                                            },
                                        }}
                                        key={uniqueKey + forceRenderMarker}
                                        position={pos}
                                    >
                                        <Tooltip>
                                            <strong className="text-lg">
                                                {earthquakeToolTip(
                                                    s as EarthquakeData,
                                                )}
                                            </strong>
                                        </Tooltip>
                                    </Marker>
                                );
                            }
                        } else {
                            const iconGaps = chosenIcon(
                                s as StationData,
                                types,
                                statuses,
                            );

                            return (
                                <Marker
                                    icon={iconGaps}
                                    key={uniqueKey}
                                    position={pos}
                                    eventHandlers={{
                                        click: () => {
                                            isNearTop(s as StationData);
                                        },
                                        popupopen: (e) => {
                                            setTimeout(() => {
                                                adjustPopupPosition(e.popup);
                                            }, 200);
                                        },
                                        popupclose: () => {
                                            setIsDaangerousPopup(false);
                                        },
                                    }}
                                >
                                    <Tooltip>
                                        <strong className="text-lg">
                                            {stationTooltip(s as StationData)}
                                        </strong>
                                    </Tooltip>
                                    <Popup
                                        maxWidth={600}
                                        minWidth={400}
                                        interactive={true}
                                    >
                                        <PopupChildren
                                            station={s as StationData}
                                            fromMain={true}
                                            mainParams={mainParams}
                                        />
                                    </Popup>
                                </Marker>
                            );
                        }
                    })}

            {toggleCoseismicVector && earthQuakeChosen !== undefined ? (
                <div
                    style={{
                        position: "absolute",
                        right: "50px",
                        bottom: "25px",
                        zIndex: 1000,
                        width: "300px",
                        pointerEvents: "auto",
                    }}
                    onMouseMove={(e) => e.stopPropagation()}
                    onTouchStart={(e) => e.stopPropagation()}
                    onTouchMove={(e) => e.stopPropagation()}
                    onTouchEnd={(e) => e.stopPropagation()}
                    onDragStart={(e) => e.preventDefault()}
                >
                    <Slider
                        classContainer="bg-zinc-50 rounded-md  p-2"
                        tittle="Vector Field Scale"
                        minValue={1}
                        maxValue={20}
                        value={vectorMagnitude}
                        suffixStyles={{ width: "1rem" }}
                        suffixValue={" "}
                        onChange={(e) => {
                            setVectorMagnitude(Number(e.target.value));
                            setLastMagnitudeVector(e.target.value);
                        }}
                    />
                </div>
            ) : null}
        </>
    );
};

const Map = ({
    initialCenter,
    posToFly,
    handleEarthquakeState,
    topoMap,
    filters,
    filterState,
    mainParams,
    mapState,
    markersByBounds,
    earthquakes,
    earthquakesFiltered,
    earthquakeAffectedStations,
    earthQuakeChosen,
    stations,
    showEarthquakeList,
    setForceSyncScrollerMap,
    setEarthquakesFiltered,
    setMarkersByBounds,
    setMainParams,
    setShowScroller,
    toggleStateEarthquakeMask,
    toggleCoseismicVector,
    vectorMagnitude,
    setVectorMagnitude,
}: MapProps) => {
    //---------------------------------------------------------------------------UseStates--------------------------------------------------------------------------------------

    const [mapProps, setMapProps] = useState<MyMapContainerProps>({
        center: [0, 0],
        zoom: 4,
        scrollWheelZoom: true,
    });

    const [forceRender, setForceRender] = useState(0);

    //---------------------------------------------------------------------------UseEffects--------------------------------------------------------------------------------
    useEffect(() => {
        if (earthquakes.length > 0) {
            setForceRender((prev) => prev + 1);
        }
    }, [earthquakes]);

    useEffect(() => {
        const savedZoomLevel = localStorage.getItem("lastZoomLevel");

        const savedPosition = localStorage.getItem("lastPosition");
        const saveMagnitudeVector = localStorage.getItem("lastMagnitudeVector");

        const pos: LatLngExpression = initialCenter
            ? initialCenter
            : savedPosition
              ? (savedPosition
                    .split(",")
                    .map((s) => parseFloat(s)) as LatLngExpression)
              : stations && stations.length > 0
                ? ([
                      stations.find((s) => s.lat && s.lon)?.lat,
                      stations.find((s) => s.lat && s.lon)?.lon,
                  ] as LatLngExpression)
                : [0, 0];

        setMapProps((prevProps) => ({
            ...prevProps,
            zoom: savedZoomLevel ? parseInt(savedZoomLevel) : 8,
            center: pos,
        }));

        setVectorMagnitude(Number(saveMagnitudeVector));

        setShowScroller(false);
    }, [mapState]);

    return (
        <div className="z-10 w-full flex justify-end">
            <MapContainer
                {...mapProps}
                key={forceRender}
                preferCanvas={true}
                maxBoundsViscosity={1.0}
                worldCopyJump={false}
                zoomControl={false}
                minZoom={3}
                className="w-full h-[92vh]"
            >
                <TileLayer
                    attribution={
                        topoMap
                            ? '&copy; <a href="https://www.opentopomap.org/copyright">OpenTopoMap</a> contributors'
                            : '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                    }
                    url={
                        topoMap
                            ? "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png"
                            : "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                    }
                />
                <ZoomControl position="bottomright" />
                <ChangeView center={mapProps.center} zoom={mapProps.zoom} />

                <MapMarkers
                    posToFly={posToFly}
                    initialCenter={initialCenter}
                    handleEarthquakeState={handleEarthquakeState}
                    filters={filters}
                    filterState={filterState}
                    mapState={mapState}
                    mainParams={mainParams}
                    markersByBounds={markersByBounds}
                    earthquakes={earthquakes}
                    earthQuakeChosen={earthQuakeChosen}
                    earthquakesFiltered={
                        earthquakesFiltered ? earthquakesFiltered : []
                    }
                    earthquakeAffectedStations={earthquakeAffectedStations}
                    stations={stations}
                    toggleStateEarthquakeMask={toggleStateEarthquakeMask}
                    toggleCoseismicVector={toggleCoseismicVector}
                    setMainParams={setMainParams}
                    setMarkersByBounds={setMarkersByBounds}
                    setEarthquakesFiltered={setEarthquakesFiltered}
                    setForceSyncScrollerMap={setForceSyncScrollerMap}
                    showEarthquakeList={showEarthquakeList}
                    setShowScroller={setShowScroller}
                    vectorMagnitude={vectorMagnitude}
                    setVectorMagnitude={setVectorMagnitude}
                />
                <ScaleControl
                    metric={true}
                    imperial={false}
                    position="bottomleft"
                />
            </MapContainer>
        </div>
    );
};

export default Map;

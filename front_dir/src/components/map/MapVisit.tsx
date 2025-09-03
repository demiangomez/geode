import { useEffect, useState } from "react";
import L, { LatLngExpression } from "leaflet";

import { MapContainer, Marker, Popup, TileLayer, useMap } from "react-leaflet";
import { PopupChildren } from "@componentsReact";

import JSZip from "jszip";

import * as toGeoJSON from "@tmcw/togeojson";

import { StationData } from "@types";

import { chosenIcon } from "@utils";

interface MyMapContainerProps {
    zoom: number;
    center: LatLngExpression;
    scrollWheelZoom: boolean;
    minZoom?: number;
    style?: React.CSSProperties;
}

interface MapProps {
    base64Data: string; // Base64 data from the database
    station: StationData;
    types: { image: string; name: string }[];
    statuses: { name: string; color: string }[];
}

const LoadKmzFromBase64 = ({ base64Data }: { base64Data: string }) => {
    const map = useMap();

    useEffect(() => {
        const loadKmzOrKmlFile = async () => {
            if (!base64Data) return;

            try {
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
                        pointToLayer: (feature, latlng) => {
                            if (feature.properties && feature.properties.icon) {
                                const customIcon = L.icon({
                                    iconUrl: feature.properties.icon,
                                    iconSize: [32, 32],
                                    iconAnchor: [16, 16],
                                });
                                return L.marker(latlng, { icon: customIcon });
                            }
                            return L.marker(latlng);
                        },
                        style: (feature) => {
                            return feature
                                ? {
                                      color: feature.properties.stroke,
                                      opacity:
                                          feature.properties["stroke-opacity"],
                                      fillColor:
                                          feature.properties["fill-color"],
                                      //   fillOpacity:
                                      //       feature.properties["fill-opacity"],
                                  }
                                : {};
                        },
                        onEachFeature: (_feature, layer) => {
                            if (
                                _feature.properties &&
                                _feature.properties.description
                            ) {
                                layer.bindPopup(
                                    _feature.properties.description,
                                );
                            }
                        },
                    });

                    map.fitBounds(geoJsonLayer.getBounds());
                    map.zoomOut(1);
                    geoJsonLayer.setStyle({
                        color: "#0000FF",
                    });
                    geoJsonLayer.addTo(map);
                };

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
            }
        };

        loadKmzOrKmlFile();
    }, [base64Data, map]);

    return null;
};

const MapVisit = ({ base64Data, station, statuses, types }: MapProps) => {
    const [mapProps, setMapProps] = useState<MyMapContainerProps>({
        zoom: 0,
        center: [0, 0],
        scrollWheelZoom: true,
        minZoom: 2,
    });

    useEffect(() => {
        const pos: LatLngExpression = station
            ? [station.lat, station.lon]
            : [0, 0];
        setMapProps({
            ...mapProps,
            center: pos,
        });
    }, [station]);

    return (
        <div className="z-10 pt-6 flex justify-center">
            <MapContainer
                {...mapProps}
                minZoom={3}
                className="w-[55vw] h-[30vh] xl:w-[40vw] lg:w-[30vw] md:w-[30vw] sm:w-[20vw]"
            >
                <TileLayer
                    attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                    minZoom={3}
                />
                <Marker
                    icon={chosenIcon(station as StationData, types, statuses)}
                    key={station ? station.lat + station.lon : "key"}
                    position={mapProps.center}
                >
                    <Popup maxWidth={1000} minWidth={200}>
                        <PopupChildren station={station} />
                    </Popup>
                </Marker>
                <LoadKmzFromBase64 base64Data={base64Data} />
            </MapContainer>
        </div>
    );
};

export default MapVisit;

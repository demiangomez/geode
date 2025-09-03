import { useEffect, useRef } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";

type Position = {
    lat: number;
    lon: number;
};

type Displacement = {
    north: number; // Desplazamiento en metros hacia el norte (componente V)
    east: number; // Desplazamiento en metros hacia el este (componente U)
};

type Props = {
    origin: Position;
    displacement: Displacement;
    magnitude: number;
};

const MapVector = ({ origin, displacement, magnitude }: Props) => {
    const map = useMap();
    const arrowRef = useRef<L.LayerGroup | null>(null);

    const arrowStyle = {
        color: "red",
        weight: 6,
        opacity: 0.8,
        pane: "overlayPane", // Usa un pane con zIndex alto
    };

    // FUNCIÓN PARA CONVERTIR METROS A GRADOS CON DEFORMACIÓN MERCATOR
    const metersToDegreesWithMercator = (
        displacement: Displacement,
        originLat: number,
    ): { deltaLat: number; deltaLon: number } => {
        // Constante: metros por grado de latitud (siempre constante)
        const METERS_PER_DEGREE_LAT = 111111; // ~111.111 km por grado

        // DEFORMACIÓN MERCATOR: metros por grado de longitud varía con latitud
        const METERS_PER_DEGREE_LON =
            METERS_PER_DEGREE_LAT * Math.cos((originLat * Math.PI) / 180);

        return {
            deltaLat: displacement.north / METERS_PER_DEGREE_LAT,
            deltaLon: displacement.east / METERS_PER_DEGREE_LON,
        };
    };

    useEffect(() => {
        if (arrowRef.current) {
            map.removeLayer(arrowRef.current);
        }

        // CALCULAR MAGNITUD REAL DEL DESPLAZAMIENTO
        // const realMagnitude = Math.sqrt(
        //     displacement.north * displacement.north +
        //         displacement.east * displacement.east,
        // );

        // Constante que multiplica la magnitud real (si usamos la real al ser tan chica no se ve)
        const size = 100000;

        // ESCALADO DEL DESPLAZAMIENTO (convertir PIXEL_SIZE a metros para tamaño fijo)
        const scaledDisplacement = {
            north: displacement.north * magnitude * size,
            east: displacement.east * magnitude * size,
        };

        // CONVERTIR DESPLAZAMIENTO A GRADOS CON DEFORMACIÓN MERCATOR
        const deltas = metersToDegreesWithMercator(
            scaledDisplacement,
            origin.lat,
        );

        // CALCULAR PUNTO FINAL CONSIDERANDO PROYECCIÓN
        const endPosition = {
            lat: origin.lat + deltas.deltaLat,
            lon: origin.lon + deltas.deltaLon,
        };

        // CALCULAR ÁNGULO DEL VECTOR
        const vectorAngle = Math.atan2(deltas.deltaLon, deltas.deltaLat);

        // CALCULAR LONGITUD DE PUNTA PROPORCIONAL EN GRADOS
        const vectorLengthInDegrees = Math.sqrt(
            Math.pow(endPosition.lat - origin.lat, 2) +
                Math.pow(endPosition.lon - origin.lon, 2),
        );

        const arrowHeadLength = vectorLengthInDegrees * 0.1;

        // CALCULAR PUNTAS EN COORDENADAS GEOGRÁFICAS
        const leftHead = [
            endPosition.lat -
                arrowHeadLength * Math.cos(vectorAngle + Math.PI / 6),
            endPosition.lon -
                arrowHeadLength * Math.sin(vectorAngle + Math.PI / 6),
        ];

        const rightHead = [
            endPosition.lat -
                arrowHeadLength * Math.cos(vectorAngle - Math.PI / 6),
            endPosition.lon -
                arrowHeadLength * Math.sin(vectorAngle - Math.PI / 6),
        ];

        // CUERPO DE LA FLECHA
        const mainLine = L.polyline(
            [
                [origin.lat, origin.lon],
                [endPosition.lat, endPosition.lon],
            ],
            arrowStyle,
        );

        // PUNTA IZQUIERDA
        const leftArrow = L.polyline(
            [
                [leftHead[0], leftHead[1]],
                [endPosition.lat, endPosition.lon],
            ],
            arrowStyle,
        );

        // PUNTA DERECHA
        const rightArrow = L.polyline(
            [
                [rightHead[0], rightHead[1]],
                [endPosition.lat, endPosition.lon],
            ],
            arrowStyle,
        );

        const group = L.layerGroup([mainLine, leftArrow, rightArrow]).addTo(
            map,
        );
        arrowRef.current = group;

        return () => {
            if (arrowRef.current) {
                map.removeLayer(arrowRef.current);
            }
        };
    }, [origin, displacement, magnitude, map]);

    return null;
};

export default MapVector;

import { Link, useMatches } from "react-router-dom";
import {
    StationData,
    StationMetadataServiceData,
    StationVisitsData,
} from "@types";

type Props = {
    state?: StationData;
    setters: {
        setStationMeta: React.Dispatch<
            React.SetStateAction<StationMetadataServiceData | undefined>
        >;
        setVisits: React.Dispatch<
            React.SetStateAction<StationVisitsData[] | undefined>
        >;
    };
};

const Breadcrumb = ({ state, setters }: Props) => {
    // FAL: 05/08/2024
    // STATE IN BREADCRUMB REFERENCE TO STATION DATA BCS ITS USED IN MAIN PAGE
    // TO RETURN IT POSITION.

    const { setStationMeta, setVisits } = setters || {};

    const handleStation = () => {
        setStationMeta(undefined);
        setVisits(undefined);
    };

    const matches = useMatches();

    const actualMatch = matches[matches.length - 1];
    const stationPath = `/${actualMatch.params.nc}/${actualMatch.params.sc}/`;

    const crumbs = matches
        .filter((match: any) => Boolean(match.handle?.crumb))
        .map((match: any) => match.handle.crumb(match));

    return (
        <div
            className={`breadcrumbs absolute left-36 peer-[.w-72]:translate-x-40 transition-all mt-4 
                badge overflow-hidden text-sm`}
        >
            <ul>
                {crumbs.map((c: string, idx: number) => {
                    return (
                        <li key={c + String(idx)}>
                            <Link
                                to={`${matches[idx].pathname}`}
                                onClick={() =>
                                    (matches as any)[idx]?.handle.crumb() ===
                                        "Station" &&
                                    actualMatch.pathname !== stationPath
                                        ? handleStation()
                                        : null
                                }
                                state={state}
                            >
                                {c}
                            </Link>
                        </li>
                    );
                })}
            </ul>
        </div>
    );
};

export default Breadcrumb;

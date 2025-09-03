import React, { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Dropdown } from "@componentsReact";

import {
    CountriesData,
    CountriesServiceData,
    GetParams,
    NetworkData,
    NetworkServiceData,
    StationData,
} from "@types";

import { getCountriesService, getNetworksService } from "@services";

import { useAuth } from "@hooks/useAuth";
import useApi from "@hooks/useApi";

interface SearchInputProps {
    stations: StationData[] | undefined;
    params: GetParams;
    setParams: React.Dispatch<React.SetStateAction<GetParams>>;
    setStation: React.Dispatch<React.SetStateAction<StationData | undefined>>;
}

const SearchInput = ({ stations, params, setParams }: SearchInputProps) => {
    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const navigate = useNavigate();

    const location = useLocation();

    const locationState = location.state as StationData;

    const [countries, setCountries] = useState<CountriesData[] | undefined>(
        undefined,
    );
    const [networks, setNetworks] = useState<NetworkData[] | undefined>(
        undefined,
    );

    const [dropdown, setDropdown] = useState<{
        type: undefined | string;
        dropdown: boolean;
    }>({ type: undefined, dropdown: false });
    const [codeSelected, setCodeSelected] = useState<string>(
        locationState?.mainParams?.country_code?.toUpperCase() ?? "",
    );
    const [networkSelected, setNetworkSelected] = useState<string>(
        locationState?.mainParams?.network_code?.toUpperCase() ?? "",
    );

    const [dropdownClassnames, setDropdownClassnames] = useState("hidden");

    const getCountries = async () => {
        const result = await getCountriesService<CountriesServiceData>(api);
        if (result) {
            setCountries(result.data);
        }
    };

    const getNetworks = async () => {
        const result = await getNetworksService<NetworkServiceData>(api);
        if (result) {
            setNetworks(result.data);
        }
    };

    useEffect(() => {
        getCountries();
        getNetworks();

        const handleClickOutside = (event: MouseEvent) => {
            const searchInput = document.getElementById("search-station");
            if (
                dropdownRef.current &&
                !dropdownRef.current.contains(event.target as Node) &&
                event.target !== searchInput
            ) {
                setDropdownClassnames("hidden");
            }
        };

        const handleSearchInputClick = () => {
            if (params.station_code) {
                setDropdownClassnames("dropdown dropdown-open w-[80%] pt-2");
            }
        };

        const searchInput = document.getElementById("search-station");
        searchInput?.addEventListener("click", handleSearchInputClick);
        document.addEventListener("mousedown", handleClickOutside);

        return () => {
            searchInput?.removeEventListener("click", handleSearchInputClick);
            document.removeEventListener("mousedown", handleClickOutside);
        };
    }, [params.station_code]);

    useEffect(() => {
        if (!locationState) {
            setNetworkSelected("");
        }
    }, [codeSelected]);

    useEffect(() => {
        if (locationState?.mainParams) {
            const stateParams = locationState?.mainParams;
            setCodeSelected(stateParams.country_code?.toUpperCase() ?? "");
            setNetworkSelected(stateParams.network_code?.toUpperCase() ?? "");
        }
    }, [locationState]);

    useEffect(() => {
        setParams({
            ...params,
            country_code: codeSelected,
            network_code: networkSelected.toLowerCase(),
            station_code: locationState?.mainParams?.station_code ?? "",
        });
    }, [networkSelected, codeSelected]);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        const searchInput = document.getElementById(
            "search-station",
        ) as HTMLInputElement;
        const { value } = searchInput;

        if (value) {
            const networkSelected = e.currentTarget.textContent?.split(".")[0];
            const codeSelected = e.currentTarget.textContent?.split(".")[1];

            const foundStation: StationData | undefined = stations?.find(
                (station) =>
                    station.network_code?.toLowerCase() ===
                        networkSelected?.toLowerCase() &&
                    station.station_code?.toLowerCase() ===
                        codeSelected?.toLowerCase(),
            );
            if (foundStation) {
                navigate(
                    `/${foundStation.network_code}/${foundStation.station_code}`,
                    { state: { ...foundStation, mainParams: params } },
                );

                // return;
            }
        }
    };

    const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
        const searchInput = document.getElementById(
            "search-station",
        ) as HTMLInputElement;
        const { textContent } = e.target as HTMLAnchorElement;

        if (textContent) {
            searchInput.value = textContent.split(".")[1];
            setDropdownClassnames("hidden");
        }

        handleSubmit(e);
    };

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const searchInput = e.target.value.trim();

        const isInputEmpty = searchInput.length === 0;
        if (isInputEmpty) {
            setDropdownClassnames("hidden");
            setParams((prev) => ({
                ...prev,
                station_code: "",
            }));
        } else {
            setParams({
                ...params,
                station_code: searchInput,
            });
            const filteredStations =
                searchInput.length !== 0
                    ? stations?.filter((station) =>
                          station.station_code
                              .toLowerCase()
                              .includes(searchInput.toLowerCase()),
                      )
                    : stations;

            const newClassnames =
                filteredStations && filteredStations.length > 0
                    ? "dropdown dropdown-open w-[80%] pt-2"
                    : "hidden";

            setDropdownClassnames(newClassnames);
        }
    };

    const dropdownRef = useRef<HTMLDivElement | null>(null);

    return (
        <div className="bg-inherit h-16 flex flex-col items-center justify-center text-black text-2xl w-6/12 self-center">
            <form
                onSubmit={handleSubmit}
                className="relative w-full h-full rounded-md bg-white flex flex-nowrap items-stretch"
            >
                <Dropdown
                    position="first"
                    type="country"
                    dropdown={dropdown}
                    data={countries}
                    dataSelected={codeSelected}
                    setDataSelected={setCodeSelected}
                    setDropdown={setDropdown}
                />
                <Dropdown
                    type="networks"
                    dropdown={dropdown}
                    data={networks}
                    dataSelected={networkSelected}
                    setDataSelected={setNetworkSelected}
                    setDropdown={setDropdown}
                />

                <div className="w-full">
                    <button
                        className="btn btn-circle absolute -top-4 z-10 left-[125px] btn-error"
                        title="Clear filters"
                        style={{
                            width: "30px",
                            height: "30px",
                            minHeight: "10px",
                        }}
                        onClick={() => {
                            const searchInput = document.getElementById(
                                "search-station",
                            ) as HTMLInputElement;
                            searchInput.value = "";
                            setDropdownClassnames("hidden");
                            setCodeSelected("");
                            setNetworkSelected("");
                            location.state
                                ? location.state.mainParams
                                    ? delete location.state.mainParams
                                    : null
                                : null;

                            setParams({
                                ...params,
                                country_code: "",
                                network_code: "",
                                station_code: "",
                            });
                        }}
                    >
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            className="h-4 w-4"
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                        >
                            <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth="2"
                                d="M6 18L18 6M6 6l12 12"
                            />
                        </svg>
                    </button>
                    <input
                        id="search-station"
                        type="text"
                        autoComplete="off"
                        className="w-full h-full peer placeholder-transparent
                    bg-transparent bg-clip-padding py-[0.25rem] pl-4
                    text-2xl font-normal leading-[1.6] text-surface outline-none transition 
                    duration-200 ease-in-out focus:z-[3] focus:border-primary 
                    focus:shadow-inset focus:outline-none motion-reduce:transition-none  
                    autofill:shadow-autofill disabled:bg-gray-200 
                    disabled:cursor-not-allowed disabled:text-gray-500 disabled:text"
                        placeholder="Search for station code"
                        aria-label="station"
                        // disabled={!networkSelected || !codeSelected}
                        title={
                            !networkSelected && codeSelected
                                ? "Select a network"
                                : networkSelected && !codeSelected
                                  ? "Select a country"
                                  : undefined
                        }
                        aria-describedby="addon-wrapping"
                        onChange={(e) => {
                            handleChange(e);
                        }}
                        value={params.station_code}
                    />
                    <label
                        className="absolute left-[170px] text-black text-xs pointer-events-none
                        transition-all peer-placeholder-shown:text-base peer-placeholder-shown:top-4
                         peer-focus:-top-0 peer-focus:text-xs peer-focus:left-[170px]"
                    >
                        Search for station code
                    </label>
                </div>
                <button
                    className="flex justify-center items-center hover:bg-gray-100 rounded-r-md w-2/12 px-2
                    disabled:bg-gray-200 
                    disabled:cursor-not-allowed disabled:text-gray-500 disabled:text
                    "
                    type="submit"
                    // disabled={!networkSelected || !codeSelected}
                >
                    <svg
                        xmlns="http://www.w3.org/2000/svg"
                        fill="none"
                        viewBox="0 0 24 24"
                        strokeWidth={1.5}
                        stroke="currentColor"
                        className="w-6 h-6"
                    >
                        <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
                        />
                    </svg>
                </button>
            </form>
            <div className={dropdownClassnames} ref={dropdownRef}>
                <ul
                    tabIndex={0}
                    className="dropdown-content z-30 menu divide-y-2 
                    divide-neutral items-center w-full text-white 
                    shadow bg-gray-800 rounded-box max-h-[400px]"
                    style={{
                        overflowY: "auto",
                        flexDirection: "column",
                        flexWrap: "nowrap",
                        overflowX: "hidden",
                    }}
                >
                    {stations?.map((station) => (
                        <li key={station?.api_id} className="text-lg w-full">
                            <a
                                onClick={(e) => {
                                    handleClick(e);
                                }}
                                className="w-full justify-center"
                            >
                                {station?.network_code?.toUpperCase() +
                                    "." +
                                    station?.station_code?.toUpperCase()}
                            </a>
                        </li>
                    ))}
                </ul>
            </div>
        </div>
    );
};

export default SearchInput;

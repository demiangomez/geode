import { Modal, Table } from "@componentsReact";
import { XMarkIcon } from "@heroicons/react/24/outline";
import { SourcesStationsData } from "@types";
import { useMemo, useState } from "react";

interface SourcesStationsTableModalProps {
    handleCloseModal: () => void;
    sourcesStations: SourcesStationsData[] | undefined;
    loading: boolean;
}

const SourcesStationsTableModal = ({
    handleCloseModal,
    sourcesStations,
    loading,
}: SourcesStationsTableModalProps) => {
    const titles: string[] = [
        "network_code",
        "station_code",
        "try_order",
        "path",
        "format",
    ];

    const [filterStationCode, setFilterStationCode] = useState<string>("");

    const [filterNetworkCode, setFilterNetworkCode] = useState<string>("");

    const [data, setData] = useState<string[][]>([]);

    useMemo(() => {
        if (sourcesStations) {
            let filteredData = sourcesStations;
            if (filterStationCode) {
                filteredData = filteredData.filter((station) =>
                    station.station_code
                        .toLowerCase()
                        .includes(filterStationCode.toLowerCase()),
                );
            }
            if (filterNetworkCode) {
                filteredData = filteredData.filter((station) =>
                    station.network_code
                        .toLowerCase()
                        .includes(filterNetworkCode.toLowerCase()),
                );
            }

            setData(
                filteredData.map((station) => [
                    station.network_code,
                    station.station_code,
                    station.try_order.toString(),
                    station.path ?? "-",
                    station.format,
                ]),
            );
        }
    }, [filterNetworkCode, filterStationCode, sourcesStations]);

    return (
        <Modal
            modalId="Sources Stations"
            size="md"
            handleCloseModal={handleCloseModal}
            close={false}
        >
            <div className="flex flex-col gap-4  overflow-y-auto h-[40vh]">
                <div className="flex flex-row justify-center items-center w-full mt-2 gap-2">
                    <label className="input flex flex-row justify-end items-center gap-2 rounded-md p-2">
                        <input
                            className="grow"
                            placeholder="Network Code"
                            value={filterNetworkCode}
                            onChange={(e) =>
                                setFilterNetworkCode(e.target.value)
                            }
                        />
                        <XMarkIcon
                            className="size-4 cursor-pointer"
                            onClick={() => setFilterNetworkCode("")}
                        />
                    </label>
                    <label className="input flex flex-row justify-end items-center gap-2 rounded-md p-2">
                        <input
                            className="grow"
                            placeholder="Station Code"
                            value={filterStationCode}
                            onChange={(e) =>
                                setFilterStationCode(e.target.value)
                            }
                        />
                        <XMarkIcon
                            className="size-4 cursor-pointer"
                            onClick={() => setFilterStationCode("")}
                        />
                    </label>
                </div>
                <Table
                    table="sources"
                    titles={data.length > 0 ? titles : []}
                    body={data.length > 0 ? data : undefined}
                    loading={loading}
                    dataOnly={true}
                    onClickFunction={() => {}}
                    deleteRegister={false}
                    dataFetchUrl="api/sources-stations"
                />
            </div>
        </Modal>
    );
};

export default SourcesStationsTableModal;

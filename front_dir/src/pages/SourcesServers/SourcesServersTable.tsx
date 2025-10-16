import {
    SourcesServersMergeModal,
    SourcesServersTableModal,
    SourcesStationsTableModal,
    Table,
    TableCard,
} from "@componentsReact";
import { getSourcesStationsByServerIdService } from "@services";
import {
    SourcesFormatData,
    SourcesServerData,
    SourcesStationsData,
    SourcesStationsServiceData,
} from "@types";
import { AxiosInstance } from "axios";
import { useEffect, useState } from "react";

interface SourcesServersPageProps {
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
    sourcesServers: SourcesServerData[] | undefined;
    sourcesFormats: SourcesFormatData[] | undefined;
    modals:
        | {
              show: boolean;
              title: string;
              type: "add" | "edit" | "none";
          }
        | undefined;
    api: AxiosInstance;
    refetch: () => void;
    loading: boolean;
}

const SourcesServersPage = ({
    setModals,
    sourcesServers,
    modals,
    sourcesFormats,
    api,
    refetch,
    loading,
}: SourcesServersPageProps) => {
    const [sourceServer, setSourceServer] = useState<
        SourcesServerData | undefined
    >(undefined);

    const [viewLoading, setViewLoading] = useState<boolean>(false);

    const [data, setData] = useState<string[][]>([]);

    const [sourcesStations, setSourcesStations] = useState<
        SourcesStationsData[] | undefined
    >(undefined);

    const titles: string[] =
        data.length > 0
            ? ["protocol", "fqdn", "username", "password", "path", "format"]
            : [];

    const handleCloseModal = () => {
        setModals(undefined);
        setSourceServer(undefined);
    };

    useEffect(() => {
        if (sourcesServers && sourcesServers.length > 0) {
            const body: string[][] = [];
            sourcesServers.forEach((sourceServer: SourcesServerData) => {
                body.push([
                    sourceServer.protocol,
                    sourceServer.fqdn,
                    sourceServer.username ?? "",
                    sourceServer.password,
                    sourceServer.path ?? "",
                    sourceServer.format,
                ]);
            });
            setData(body);
        }
    }, [sourcesServers]);

    const onViewClickFunction = () => {
        setModals({
            show: true,
            title: "Sources Stations",
            type: "edit",
        });
    };

    const getSourcesStationsByServerId = async () => {
        try {
            setViewLoading(true);
            const res =
                await getSourcesStationsByServerIdService<SourcesStationsServiceData>(
                    api,
                    sourceServer?.server_id as number,
                );
            if (res && res.statusCode === 200) {
                setSourcesStations(res.data);
            }
        } catch (error) {
            console.error(error);
        } finally {
            setViewLoading(false);
        }
    };

    useEffect(() => {
        if (
            modals &&
            modals.show &&
            modals.title === "Sources Stations" &&
            sourceServer
        ) {
            getSourcesStationsByServerId();
        }
    }, [sourceServer]);

    return (
        <TableCard
            title={"Sources Servers"}
            size={"100%"}
            addButtonTitle="+ Source Server"
            setModals={setModals}
            addButton={true}
            modalTitle="Sources Servers"
            secondAddButton={true}
            secondAddButtonTitle="Transfer Stations"
            secondModalTitle="Merge Source Server"
        >
            <Table
                table="servers"
                titles={titles}
                body={data.length > 0 ? data : undefined}
                loading={loading}
                onClickFunction={() =>
                    setModals({
                        show: true,
                        title: "Sources Servers",
                        type: "edit",
                    })
                }
                deleteRegister={false}
                state={sourcesServers}
                setState={setSourceServer}
                viewRegister={true}
                onViewClickFunction={onViewClickFunction}
                dataFetchUrl="api/sources-servers"
            />
            {modals && modals.show && modals.title === "Sources Servers" && (
                <SourcesServersTableModal
                    handleClose={handleCloseModal}
                    type={modals?.type}
                    refetch={refetch}
                    sourcesFormats={sourcesFormats}
                    sourceServer={sourceServer}
                    api={api}
                />
            )}
            {modals?.show && modals.title === "Merge Source Server" && (
                <SourcesServersMergeModal
                    sourcesServers={sourcesServers}
                    handleCloseModal={() => {
                        setModals(undefined);
                        setSourceServer(undefined);
                    }}
                    refetch={refetch}
                    api={api}
                />
            )}
            {modals?.show && modals.title === "Sources Stations" && (
                <SourcesStationsTableModal
                    handleCloseModal={() => {
                        setModals(undefined);
                        setSourceServer(undefined);
                    }}
                    loading={viewLoading}
                    sourcesStations={sourcesStations}
                />
            )}
        </TableCard>
    );
};

export default SourcesServersPage;

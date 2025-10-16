import { SourcesFormatsTableModal, Table, TableCard } from "@components/index";
import { SourcesFormatData } from "@types";
import { AxiosInstance } from "axios";
import { useEffect, useState } from "react";

interface SourcesFormatsPageProps {
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
    modals:
        | {
              show: boolean;
              title: string;
              type: "add" | "edit" | "none";
          }
        | undefined;
    sourcesFormats: SourcesFormatData[] | undefined;
    api: AxiosInstance;
    loading: boolean;
    refetch: () => void;
}

const SourcesFormatsPage = ({
    setModals,
    sourcesFormats,
    api,
    modals,
    refetch,
    loading,
}: SourcesFormatsPageProps) => {
    const [sourceFormat, setSourceFormat] = useState<
        SourcesFormatData | undefined
    >(undefined);

    const [data, setData] = useState<string[][]>([]);

    const titles: string[] = data.length > 0 ? ["format"] : [];

    const handleEdit = () => {
        setModals({
            show: true,
            title: "Source Format",
            type: "edit",
        });
    };
    useEffect(() => {
        if (sourcesFormats && sourcesFormats.length > 0) {
            const body: string[][] = [];
            sourcesFormats
                .sort((a, b) => a.format.localeCompare(b.format))
                .map((sourceFormat: SourcesFormatData) => {
                    body.push([sourceFormat.format]);
                });
            setData(body);
        }
    }, [sourcesFormats]);

    return (
        <TableCard
            title={"Sources Formats"}
            size={"100%"}
            addButtonTitle="+ Source Format"
            setModals={setModals}
            addButton={true}
            modalTitle="Source Format"
        >
            <Table
                table="formats"
                titles={titles}
                body={data.length > 0 ? data : undefined}
                loading={loading}
                onClickFunction={handleEdit}
                deleteRegister={false}
                state={sourcesFormats}
                setState={setSourceFormat}
                dataFetchUrl="api/sources-formats"
            />
            {modals?.show && modals.title === "Source Format" && (
                <SourcesFormatsTableModal
                    sourceFormat={sourceFormat}
                    handleClose={() => {
                        setModals(undefined);
                        setSourceFormat(undefined);
                    }}
                    refetch={refetch}
                    api={api}
                    type={modals?.type}
                />
            )}
        </TableCard>
    );
};

export default SourcesFormatsPage;

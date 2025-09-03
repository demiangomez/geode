import { useMemo, useState } from "react";
import { Spinner } from "@componentsReact";

import { ClipboardDocumentIcon } from "@heroicons/react/24/outline";

import { formatValue, isValidDate } from "@utils";

import { usePopup } from "@hooks";

import { StationEvents } from "@types";

interface Props {
    loading: boolean;
    body: any[][] | undefined;
    titles: string[];
    events: StationEvents[] | undefined;
    onClickFunction: (event: any) => void;
}

const EventsTable = ({
    loading,
    body,
    titles,
    events,
    onClickFunction,
}: Props) => {
    const { showPopup, show } = usePopup(2000);

    const [copiedEventId, setCopiedEventId] = useState<string | null>(null);

    const copyText = async (event: StationEvents) => {
        try {
            await navigator.clipboard.writeText(event.description ?? "");
            setCopiedEventId(event.event_id);
            show();
        } catch (error) {
            alert("Failed to copy text");
            console.error(error);
        }
    };

    const memoizedBody = useMemo(() => {
        return body?.map(
            (row) => row.slice(1).map((data) => formatValue(data)), // Remove the first element of the row bcs it's the id
        );
    }, [body]);

    return (
        <div>
            <table className="w-full table z-10 table-zebra bg-neutral-content">
                <thead className="">
                    <tr>
                        {titles.length === 0 && !loading && (
                            <th className="text-center text-neutral text-2xl">
                                There are no Events to show
                            </th>
                        )}

                        {titles.map((title, index) => (
                            <th
                                className="text-center text-neutral max-w-[200px]"
                                key={index}
                            >
                                {title.toUpperCase()}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody className="">
                    {loading ? (
                        <tr>
                            <td
                                colSpan={titles.length + 1}
                                className="relative h-[200px]"
                            >
                                <div className="absolute inset-0 flex justify-center items-center">
                                    <Spinner size="lg" />
                                </div>
                            </td>
                        </tr>
                    ) : (
                        memoizedBody?.map((row, rowIndex) => (
                            <tr
                                key={rowIndex + 1}
                                className={"cursor-pointer hover"}
                            >
                                {row.map((data, dataIndex) => {
                                    const isDescription =
                                        titles[dataIndex] === "description";

                                    const valueUnformatted =
                                        body?.[rowIndex]?.slice(1)?.[dataIndex]; // slice bcs the first element is the id

                                    const event = events?.filter(
                                        (e) =>
                                            e.event_id ===
                                            body?.[rowIndex]?.[0],
                                    )[0];

                                    return (
                                        <td
                                            key={dataIndex}
                                            className={`text-center z-10 ${isDescription ? " flex items-center justify-center mx-auto relative" : "w-fit"}`}
                                            onClick={() => {
                                                onClickFunction(event);
                                            }}
                                            title={
                                                isValidDate(data)
                                                    ? formatValue(
                                                          valueUnformatted,
                                                      )
                                                    : valueUnformatted
                                            }
                                        >
                                            <span
                                                className={`${isDescription ? "w-[115px]" : ""}`}
                                            >
                                                {data}{" "}
                                            </span>
                                            {isDescription && (
                                                <div
                                                    className={` ${showPopup && copiedEventId === event?.event_id ? "tooltip tooltip-open ml-3" : "inline-block ml-3"}`}
                                                    data-tip="Copied !"
                                                >
                                                    <div className="w-full flex items-center justify-start">
                                                        <ClipboardDocumentIcon
                                                            className="size-6 xl:flex rounded-md transition-all duration-75 btn-ghost hover:scale-125"
                                                            onClick={(e) => {
                                                                e.stopPropagation();

                                                                copyText(
                                                                    event as StationEvents,
                                                                );
                                                            }}
                                                        />
                                                    </div>
                                                </div>
                                            )}
                                        </td>
                                    );
                                })}
                            </tr>
                        ))
                    )}
                </tbody>
            </table>
        </div>
    );
};

export default EventsTable;

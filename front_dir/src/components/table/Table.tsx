//React
import { useState } from "react";
import { useNavigate } from "react-router-dom";

//Components
import { Spinner } from "@componentsReact";

//Utils
import { dateFromDay, formattedDates } from "@utils";

//Hooks
import { useUser } from "@hooks";

//Icons
import {
    BookOpenIcon,
    EyeIcon,
    EyeSlashIcon,
    TrashIcon,
} from "@heroicons/react/24/outline";
import { findFlagUrlByIso3Code } from "country-flags-svg-v2";

interface TableProps {
    table: string;
    loading?: boolean;
    dataOnly?: boolean;
    buttonRegister?: boolean;
    visitsRegister?: boolean;
    deleteRegister?: boolean;
    viewRegister?: boolean;
    multipleSelect?: boolean;
    titles: string[];
    body: any[][] | undefined;
    alterInfo?: any;
    state?: any;
    setState?: any;
    dataFetchUrl?: string;
    onAlterClickFunction?: () => void;
    onClickFunction: () => void;
    onVisitsClickFunction?: () => void;
    onViewClickFunction?: () => void;
    onClickFunctionWithValue?: (value: any) => void;
    selectAction?: (rowData: any[]) => void;
}

const Table = ({
    table,
    loading,
    dataOnly,
    buttonRegister,
    deleteRegister,
    titles,
    body,
    alterInfo,
    state,
    dataFetchUrl,
    onClickFunction,
    onAlterClickFunction,
    onVisitsClickFunction,
    onClickFunctionWithValue,
    setState,
    visitsRegister,
    viewRegister,
    multipleSelect,
    onViewClickFunction,
    selectAction,
}: TableProps) => {
    const {
        state: { unauthorizedOperations },
    } = useUser();

    const navigate = useNavigate();

    const [visibleTooltipIndex, setVisibleTooltipIndex] = useState<
        number | null
    >(null);

    const [showPassword, setShowPassword] = useState<number | null>(null);

    const [indexCheked, setIndexCheked] = useState<number[]>([]);

    // verifico si hay errores de auth para metodo get, ya que la tabla solo recibe datos

    const unauthorizedOperationsKey = dataFetchUrl + "-get";

    const authErrorForThisTable =
        dataFetchUrl &&
        unauthorizedOperations[unauthorizedOperationsKey] &&
        unauthorizedOperations[unauthorizedOperationsKey].method === "get"
            ? unauthorizedOperations[unauthorizedOperationsKey].msg
            : null;

    return (
        <div
            className={
                visibleTooltipIndex === null ? `overflow-x-auto pb-2` : ""
            }
        >
            {authErrorForThisTable ? (
                <div className="text-center text-neutral text-xl font-bold w-full rounded-md bg-neutral-content p-6">
                    {authErrorForThisTable}
                </div>
            ) : (
                <table className="table table-zebra bg-neutral-content">
                    {table === "Station" && (
                        <caption className="py-2 truncate text-base-content text-[13px] font-light text-start">
                            RX as Receiver, ANT as Antenna, HC as Height Code,
                            RAD as Radome Code
                        </caption>
                    )}
                    <thead className="">
                        <tr>
                            {titles.length > 0 ? (
                                <>
                                    {!dataOnly && !deleteRegister && (
                                        <th className="text-center text-neutral w-fit">
                                            Modify
                                        </th>
                                    )}
                                    {dataOnly && deleteRegister && (
                                        <th className="text-center text-neutral"></th>
                                    )}
                                    {visitsRegister && (
                                        <th className="text-center text-neutral">
                                            Visits
                                        </th>
                                    )}
                                    {buttonRegister && (
                                        <th className="text-center text-neutral">
                                            Add Visit
                                        </th>
                                    )}
                                    {viewRegister && (
                                        <th className="text-center text-neutral">
                                            {table === "people"
                                                ? "Detail"
                                                : "Relations"}
                                        </th>
                                    )}
                                    {multipleSelect && (
                                        <th className="text-center text-neutral">
                                            <label className="label">
                                                Check All
                                                <input
                                                    type="checkbox"
                                                    checked={
                                                        indexCheked.length ===
                                                        body?.length
                                                    }
                                                    onClick={() => {
                                                        if (
                                                            onAlterClickFunction !==
                                                            undefined
                                                        ) {
                                                            onAlterClickFunction();
                                                            if (
                                                                indexCheked.length !==
                                                                body?.length
                                                            ) {
                                                                const all =
                                                                    Array.from(
                                                                        {
                                                                            length:
                                                                                body?.length ??
                                                                                0,
                                                                        },
                                                                        (
                                                                            _,
                                                                            i,
                                                                        ) => i,
                                                                    );
                                                                setIndexCheked(
                                                                    all,
                                                                );
                                                            } else {
                                                                setIndexCheked(
                                                                    [],
                                                                );
                                                            }
                                                        }
                                                    }}
                                                    className="checkbox ml-2"
                                                />
                                            </label>
                                        </th>
                                    )}
                                </>
                            ) : (
                                !loading &&
                                body &&
                                body.length === 0 && (
                                    <th className="text-center text-neutral text-2xl">
                                        {table === "People"
                                            ? "There are no people associated to this station"
                                            : table === "PeopleRelations"
                                              ? "There are no people relations"
                                              : `There is no information for this ${table}`}
                                    </th>
                                )
                            )}

                            {titles.map((title, index) => (
                                <th
                                    className={`text-center text-neutral ${
                                        title?.toLowerCase() === "path" ||
                                        (title?.toLowerCase() === "name" &&
                                            table !== "People")
                                            ? "max-w-lg w-full"
                                            : title?.toLowerCase() === "name" &&
                                                table === "People"
                                              ? "max-w-[200px] w-[20%]"
                                              : "max-w-[200px]"
                                    }`}
                                    key={index}
                                >
                                    {title
                                        ? title
                                              ?.toUpperCase()
                                              .replace(/_/g, " ")
                                              .replace("ANTENNA", "ANT")
                                              .replace("RECEIVER", "RX")
                                              .replace("RX FIRMWARE", "RX FW")
                                              .replace("HEIGHT CODE", "HC")
                                              .replace("RADOME CODE", "RAD")
                                              .replace(
                                                  /ANT (HEIGHT|NORTH|EAST)/g,
                                                  "$1",
                                              )
                                        : ""}
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
                            body?.map((row, index) => (
                                <tr
                                    key={index + 1}
                                    className={`${dataOnly && "cursor-pointer hover"}`}
                                >
                                    {selectAction && (
                                        <td
                                            key={`select-${index}`}
                                            className="text-center"
                                        >
                                            <button
                                                className="btn btn-sm btn-circle btn-ghost text-success"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    selectAction(row);
                                                }}
                                            >
                                                ✓
                                            </button>
                                        </td>
                                    )}
                                    {!dataOnly && !deleteRegister ? (
                                        <td key={index} className="text-center">
                                            <button
                                                className="btn btn-sm btn-circle btn-ghost"
                                                onClick={() => {
                                                    onClickFunction();
                                                    setState(state?.[index]);
                                                }}
                                            >
                                                📝
                                            </button>
                                        </td>
                                    ) : dataOnly && deleteRegister ? (
                                        <td key={index} className="text-center">
                                            <button
                                                className="btn btn-sm btn-square btn-ghost"
                                                onClick={() => {
                                                    onClickFunction();
                                                    setState(state?.[index]);
                                                }}
                                            >
                                                <TrashIcon className="size-6 text-red-600" />
                                            </button>
                                        </td>
                                    ) : (
                                        dataOnly &&
                                        multipleSelect && (
                                            <td
                                                key={index}
                                                className="text-center"
                                            >
                                                <input
                                                    type="checkbox"
                                                    className="checkbox"
                                                    checked={indexCheked.includes(
                                                        index,
                                                    )}
                                                    onClick={() => {
                                                        if (
                                                            row !== undefined &&
                                                            onClickFunctionWithValue
                                                        ) {
                                                            onClickFunctionWithValue(
                                                                row,
                                                            );
                                                        }
                                                        setIndexCheked(
                                                            (
                                                                prev: number[],
                                                            ) => {
                                                                if (
                                                                    prev.includes(
                                                                        index,
                                                                    )
                                                                ) {
                                                                    return prev.filter(
                                                                        (
                                                                            i: number,
                                                                        ) =>
                                                                            i !==
                                                                            index,
                                                                    );
                                                                } else {
                                                                    return [
                                                                        ...prev,
                                                                        index,
                                                                    ];
                                                                }
                                                            },
                                                        );
                                                    }}
                                                />
                                            </td>
                                        )
                                    )}
                                    {viewRegister && (
                                        <td
                                            key={index + "view"}
                                            className="text-center"
                                        >
                                            <button
                                                className="btn btn-sm btn-circle btn-ghost"
                                                onClick={() => {
                                                    setState(state?.[index]);
                                                    onViewClickFunction?.();
                                                }}
                                            >
                                                <BookOpenIcon
                                                    className="size-6"
                                                    onClick={() => {}}
                                                />
                                            </button>
                                        </td>
                                    )}
                                    {visitsRegister && (
                                        <td
                                            key={index + "visits"}
                                            className="text-center"
                                        >
                                            <div
                                                onClick={() => {
                                                    onVisitsClickFunction?.();
                                                    setState(state?.[index]);
                                                }}
                                                className="btn btn-sm btn-circle btn-ghost"
                                            >
                                                📅
                                            </div>
                                        </td>
                                    )}
                                    {buttonRegister && (
                                        <td
                                            key={index + "add_visit"}
                                            className="text-center"
                                        >
                                            <button
                                                className="btn btn-sm btn-circle btn-ghost"
                                                onClick={() => {
                                                    onAlterClickFunction &&
                                                        onAlterClickFunction();
                                                    setState(state?.[index]);
                                                }}
                                            >
                                                Add
                                            </button>
                                        </td>
                                    )}
                                    {row.map(
                                        (
                                            val: string | boolean | number,
                                            idx: number,
                                        ) => {
                                            const isDateFunc = (val: any) => {
                                                const isoDateRegex =
                                                    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$/;
                                                return (
                                                    typeof val === "string" &&
                                                    isoDateRegex.test(val)
                                                );
                                            };

                                            const isDoyFormat = (val: any) => {
                                                // Regex para formato DOY: YYYY DDD HH MM SS
                                                const doyRegex =
                                                    /^\d{4}\s+\d{1,3}\s+\d{2}\s+\d{2}\s+\d{2}$/;
                                                return (
                                                    typeof val === "string" &&
                                                    doyRegex.test(val.trim())
                                                );
                                            };

                                            const formatDoyDate = (
                                                doyString: string,
                                            ) => {
                                                try {
                                                    const date =
                                                        dateFromDay(doyString);
                                                    return date
                                                        ? formattedDates(date)
                                                        : doyString;
                                                } catch {
                                                    return doyString;
                                                }
                                            };

                                            const isDate =
                                                isDateFunc(val) && val !== "";
                                            const isDoy =
                                                isDoyFormat(val) && val !== "";

                                            const flag =
                                                titles[idx] ===
                                                    "country_code" &&
                                                val &&
                                                findFlagUrlByIso3Code(
                                                    val as string,
                                                );

                                            const base64Str =
                                                "data:image/png;base64,";

                                            return (
                                                <td
                                                    key={idx}
                                                    title={
                                                        titles[idx] !== "Visit"
                                                            ? isDate
                                                                ? formattedDates(
                                                                      new Date(
                                                                          val as string,
                                                                      ),
                                                                  )
                                                                : isDoy
                                                                  ? formatDoyDate(
                                                                        val as string,
                                                                    )
                                                                  : (String(
                                                                        val,
                                                                    ) ?? "")
                                                            : ""
                                                    }
                                                    className={`text-center max-w-[200px] overflow-visible text-ellipsis whitespace-nowrap
                    ${titles[idx] === "country_code" && "flex justify-center"}
                    ${row?.[idx] === false ? "text-red-600" : row?.[idx] === true && "text-green-600"}
                        `}
                                                    onClick={() => {
                                                        dataOnly &&
                                                            table ===
                                                                "Stations" &&
                                                            navigate(
                                                                `/${state?.[index].network_code}/${state?.[index].station_code}`,
                                                                {
                                                                    state: {
                                                                        station:
                                                                            state?.[
                                                                                index
                                                                            ],
                                                                        mainParams:
                                                                            alterInfo,
                                                                    },
                                                                },
                                                            );
                                                    }}
                                                >
                                                    {titles[idx] === "Color" &&
                                                        val && (
                                                            <div className="flex justify-center">
                                                                <div
                                                                    className={
                                                                        val as string
                                                                    }
                                                                    style={{
                                                                        width: "40px",
                                                                        height: "40px",
                                                                        backgroundColor:
                                                                            "#000",
                                                                        borderRadius:
                                                                            "50%",
                                                                    }}
                                                                ></div>
                                                            </div>
                                                        )}
                                                    {titles[idx] ===
                                                        "comments" &&
                                                        val && (
                                                            <div
                                                                className="overflow-y-auto overflow-x-auto pl-8 max-h-32"
                                                                dangerouslySetInnerHTML={{
                                                                    __html:
                                                                        val ??
                                                                        "",
                                                                }}
                                                            />
                                                        )}
                                                    {titles[idx] ===
                                                        "country_code" &&
                                                        val && (
                                                            <img
                                                                width={30}
                                                                height={30}
                                                                className="mr-2"
                                                                src={`${flag}`}
                                                            />
                                                        )}
                                                    {titles[idx] === "Image" &&
                                                        val && (
                                                            <div className="avatar">
                                                                <div className="w-14 mask mask-squircle ">
                                                                    <img
                                                                        src={
                                                                            base64Str +
                                                                            val
                                                                        }
                                                                    />
                                                                </div>
                                                            </div>
                                                        )}

                                                    {val !== "" &&
                                                    val != null &&
                                                    titles[idx] !== "Photo" &&
                                                    titles[idx] !== "Visit" &&
                                                    titles[idx] !==
                                                        "comments" &&
                                                    titles[idx] !== "Image" &&
                                                    titles[idx] !== "Color" ? (
                                                        titles[idx] ===
                                                        "password" ? (
                                                            <div className="overflow-hidden text-ellipsis flex flex-row justify-center items-center gap-">
                                                                <input
                                                                    type={
                                                                        showPassword ===
                                                                        index
                                                                            ? "text"
                                                                            : "password"
                                                                    }
                                                                    value={
                                                                        val as string
                                                                    }
                                                                    readOnly
                                                                    className="bg-transparent w-20"
                                                                />
                                                                <button
                                                                    className="btn btn-xs btn-ghost"
                                                                    onClick={(
                                                                        e,
                                                                    ) => {
                                                                        e.stopPropagation();
                                                                        setShowPassword(
                                                                            showPassword ===
                                                                                index
                                                                                ? null
                                                                                : index,
                                                                        );
                                                                    }}
                                                                >
                                                                    {showPassword ===
                                                                    index ? (
                                                                        <EyeSlashIcon className="size-6 self-center" />
                                                                    ) : (
                                                                        <EyeIcon className="size-6 self-center" />
                                                                    )}
                                                                </button>
                                                            </div>
                                                        ) : titles[idx] ===
                                                              "path" ||
                                                          titles[idx] ===
                                                              "server" ||
                                                          titles[idx] ===
                                                              "fqdn" ||
                                                          titles[idx] ===
                                                              "Name" ? (
                                                            <div className="w-full  overflow-auto whitespace-wrap">
                                                                {val}
                                                            </div>
                                                        ) : typeof val ===
                                                              "string" &&
                                                          titles[idx] !==
                                                              "Image" &&
                                                          titles[idx] !==
                                                              "Color" ? (
                                                            <div className="overflow-hidden text-ellipsis">
                                                                {val?.length >
                                                                    15 &&
                                                                !isDate &&
                                                                !isDoy
                                                                    ? val?.substring(
                                                                          0,
                                                                          15,
                                                                      ) + "..."
                                                                    : isDate
                                                                      ? formattedDates(
                                                                            new Date(
                                                                                val,
                                                                            ),
                                                                        )
                                                                      : isDoy
                                                                        ? formatDoyDate(
                                                                              val as string,
                                                                          )
                                                                        : val}
                                                            </div>
                                                        ) : typeof val ===
                                                          "boolean" ? (
                                                            val ? (
                                                                "✔"
                                                            ) : (
                                                                "✘"
                                                            )
                                                        ) : typeof val ===
                                                          "number" ? (
                                                            val
                                                        ) : (
                                                            "-"
                                                        )
                                                    ) : val !== "" &&
                                                      val !== null &&
                                                      titles[idx] ===
                                                          "Photo" ? (
                                                        <div className="avatar">
                                                            <div className="w-14 mask mask-squircle ">
                                                                <img
                                                                    src={
                                                                        base64Str +
                                                                        val
                                                                    }
                                                                />
                                                            </div>
                                                        </div>
                                                    ) : val !== "" &&
                                                      val !== null &&
                                                      typeof val === "string" &&
                                                      titles[idx] ===
                                                          "Visit" ? (
                                                        <div
                                                            className="relative group"
                                                            onMouseEnter={() =>
                                                                setVisibleTooltipIndex(
                                                                    index,
                                                                )
                                                            }
                                                            onMouseLeave={() =>
                                                                setVisibleTooltipIndex(
                                                                    null,
                                                                )
                                                            }
                                                        >
                                                            <div>
                                                                {val?.length >
                                                                    15 &&
                                                                !isDate &&
                                                                !isDoy
                                                                    ? val?.substring(
                                                                          0,
                                                                          15,
                                                                      ) + "..."
                                                                    : isDate
                                                                      ? formattedDates(
                                                                            new Date(
                                                                                val,
                                                                            ),
                                                                        )
                                                                      : isDoy
                                                                        ? formatDoyDate(
                                                                              val,
                                                                          )
                                                                        : val}
                                                            </div>
                                                        </div>
                                                    ) : titles[idx] !==
                                                          "comments" &&
                                                      titles[idx] !== "Image" &&
                                                      titles[idx] !==
                                                          "Color" ? (
                                                        "-"
                                                    ) : null}
                                                </td>
                                            );
                                        },
                                    )}
                                </tr>
                            ))
                        )}
                    </tbody>
                </table>
            )}
        </div>
    );
};

export default Table;

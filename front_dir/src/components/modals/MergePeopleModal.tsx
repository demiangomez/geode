import { useEffect, useState } from "react";

import { Spinner, Modal, Alert } from "@componentsReact";

import { mergePeopleService } from "@services";

import {
    ArrowRightIcon,
    MagnifyingGlassIcon,
} from "@heroicons/react/24/outline";

import { useAuth, useApi } from "@hooks";

import { People, Errors } from "@types";

interface MergePeopleModalProps {
    setStateModal: React.Dispatch<
        React.SetStateAction<
            | { show: boolean; title: string; type: "add" | "edit" | "none" }
            | undefined
        >
    >;
    handleCloseModal: () => void;
    body: People[] | undefined;
}

type ErrorMerge = {
    type: string;
    errors: Errors;
};

type MergeSucces = {
    statusCode: number;
    message: string;
};

const MergePeopleModal = ({
    setStateModal,
    handleCloseModal,
    body,
}: MergePeopleModalProps) => {
    const searchPlaceholder = "Search by Name, Last Name...";

    const [loading, setLoading] = useState<boolean>(false);

    const [people, setPeople] = useState<
        { id: string; name: string }[] | undefined
    >(undefined);

    const [searchFrom, setSearchFrom] = useState<
        { id: string; name: string }[] | undefined
    >(people);

    const [searchTo, setSearchTo] = useState<
        { id: string; name: string }[] | undefined
    >(people);

    const searchPeople = (search: string, type: string) => {
        const valueSearched = people?.filter((p) =>
            p.name.toLowerCase().includes(search.toLowerCase()),
        );

        switch (type) {
            case "From":
                setSearchFrom(valueSearched);
                break;
            case "To":
                setSearchTo(valueSearched);
                break;
        }
    };

    const [msg, setMsg] = useState<
        { status: number; msg: string; errors?: Errors } | undefined
    >(undefined);

    const [from, setFrom] = useState<string | number | undefined>(undefined);
    const [to, setTo] = useState<string | number | undefined>(undefined);

    const { token, logout } = useAuth();
    const api = useApi(token, logout);

    const getFullName = (name: string, surname: string) => {
        return `${name} ${surname}`;
    };
    const findPerson = (id: string | number) => {
        return body?.find((person) => person && person.id === Number(id));
    };

    const mergePeople = async () => {
        try {
            setLoading(true);
            if (
                typeof from === "string" &&
                typeof to === "string" &&
                from !== to &&
                findPerson(from) &&
                findPerson(to)
            ) {
                const res = await mergePeopleService<ErrorMerge | MergeSucces>(
                    api,
                    {
                        from: Number(from),
                        to: Number(to),
                    },
                );

                if ("message" in res) {
                    setMsg({
                        status: res.statusCode,
                        msg: res.message,
                    });
                } else {
                    setMsg({
                        status: 500,
                        msg: "There was a server error",
                        errors: res.errors,
                    });
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        mergePeople();
    };

    const filterPeople = (people: People[]) => {
        return people
            .filter((person) => person)
            .map((person) => ({
                id: person.id.toString(),
                name: getFullName(person.first_name, person.last_name),
            }));
    };

    useEffect(() => {
        if (body && body.length > 0) {
            const filteredPeople = filterPeople(body);
            filteredPeople.sort((a, b) => a.name.localeCompare(b.name));
            setPeople(filteredPeople);
        }
    }, [body]);

    useEffect(() => {
        if (Number(from) === Number(to)) {
            setMsg({
                status: 400,
                msg: "¡Merge same person is not possible!",
                errors: {
                    errors: [
                        {
                            code: "400",
                            detail: "",
                            attr: "merge",
                        },
                    ],
                    type: "MergeError",
                },
            });
        } else {
            setMsg(undefined);
        }
    }, [from, to]);

    return (
        <Modal
            close={false}
            modalId={"MergePeople"}
            size={"smPlus"}
            handleCloseModal={() => handleCloseModal()}
            setModalState={setStateModal}
            noPadding={true}
        >
            {" "}
            <div>
                <div className="w-full border-b-2 border-gray-300 pb-6 pl-8 pt-6">
                    <h1 className="text-2xl font-bold">Merge People</h1>
                </div>
                {
                    <form
                        typeof="submit"
                        onSubmit={(e) => {
                            handleSubmit(e);
                        }}
                        className="w-full flex justify-start items-center flex-col gap-4"
                    >
                        <div className="flex justify-start flex-row items-center border-b-2 mt-4 border-gray-300 w-full pb-6">
                            <div className="flex flex-col gap-6 w-full max-w-[500px] pl-6">
                                <div className="flex flex-col gap-2 items-center justify-center p-3">
                                    <h2 className="text-xl font-semibold">
                                        Merge From:
                                    </h2>
                                    <div className="relative w-full">
                                        <input
                                            type="text"
                                            placeholder={searchPlaceholder}
                                            className="input input-bordered w-full pr-10"
                                            onChange={(e) =>
                                                searchPeople(
                                                    e.target.value,
                                                    "From",
                                                )
                                            }
                                        />
                                        <MagnifyingGlassIcon
                                            className="absolute right-3 top-1/2 transform -translate-y-1/2 text-gray-400"
                                            width={16}
                                            height={16}
                                        />
                                    </div>
                                </div>
                                <div className="h-[20vh] overflow-y-auto">
                                    {(searchFrom && searchFrom.length > 0
                                        ? searchFrom
                                        : people
                                    )?.map((person) => (
                                        <div
                                            key={person.id}
                                            className="flex flex-row gap-4 p-2 mb-1 hover:bg-gray-200 rounded-md"
                                        >
                                            <input
                                                className="checkbox"
                                                type="checkbox"
                                                name="person-from"
                                                value={person.id}
                                                checked={from === person.id}
                                                onChange={() =>
                                                    from === person.id
                                                        ? setFrom(undefined)
                                                        : setFrom(person.id)
                                                }
                                            />
                                            <label className="text-xl">
                                                {person.name}
                                            </label>
                                        </div>
                                    ))}
                                </div>
                            </div>
                            <div className="flex flex-col gap-6 w-full max-w-[500px] pl-6">
                                <div className="flex flex-col gap-2 items-center justify-center p-3">
                                    <h2 className="text-xl font-semibold">
                                        Merge To:
                                    </h2>
                                    <div className="relative w-full">
                                        <input
                                            type="text"
                                            placeholder={searchPlaceholder}
                                            className="input input-bordered w-full pr-10"
                                            onChange={(e) =>
                                                searchPeople(
                                                    e.target.value,
                                                    "To",
                                                )
                                            }
                                        />
                                        <MagnifyingGlassIcon
                                            className="absolute right-3 top-1/2 transform -translate-y-1/2 text-gray-400"
                                            width={16}
                                            height={16}
                                        />
                                    </div>
                                </div>

                                <div className="h-[20vh] overflow-y-auto">
                                    {(searchTo && searchTo.length > 0
                                        ? searchTo
                                        : people
                                    )?.map((person) => (
                                        <div
                                            key={person.id}
                                            className="flex flex-row justify-start items-center gap-4 p-2 mb-1 hover:bg-gray-200 rounded-md"
                                        >
                                            <input
                                                className="checkbox"
                                                type="checkbox"
                                                name="person-to"
                                                value={person.id}
                                                checked={to === person.id}
                                                onChange={() =>
                                                    to === person.id
                                                        ? setTo(undefined)
                                                        : setTo(person.id)
                                                }
                                            />
                                            <label className="text-xl">
                                                {person.name}
                                            </label>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                        {msg && (
                            <div className="w-full px-4">
                                <Alert msg={msg} />
                            </div>
                        )}

                        <div className="flex justify-center flex-col w-full items-center gap-1 mb-4">
                            <button
                                disabled={
                                    !(to && from) ||
                                    to === from ||
                                    (msg && "message" in msg)
                                }
                                className="btn btn-neutral w-32"
                                type="submit"
                            >
                                {loading && <Spinner size="lg" />}
                                {!loading && (
                                    <div className="flex justify-center items-center gap-1">
                                        <p className="font-semibold text-base">
                                            Merge
                                        </p>
                                        <ArrowRightIcon className="size-5" />
                                    </div>
                                )}
                            </button>
                        </div>
                    </form>
                }
            </div>
        </Modal>
    );
};

export default MergePeopleModal;

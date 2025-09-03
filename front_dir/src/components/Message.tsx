import { useEffect, useState } from "react";

interface MessageProps {
    error: boolean | undefined;
    msg: string | undefined;
}

const Message = ({ error, msg }: MessageProps) => {
    const [value, setValue] = useState(0);
    const [show, setShow] = useState(true);

    const alertType = error
        ? "inline-flex items-center justify-center flex-shrink-0 w-8 h-8 rounded-lg bg-red-800 text-red-200"
        : "inline-flex items-center justify-center flex-shrink-0 w-8 h-8 rounded-lg bg-green-800 text-green-200";

    const closeToast = () => {
        setShow(false);
        setValue(0);
    };

    const MAX = 100;

    useEffect(() => {
        setValue(0);
        setShow(true);

        const interval = setInterval(() => {
            setValue((prevValue) => {
                const newValue = prevValue + MAX / 145;
                if (newValue >= MAX) {
                    clearInterval(interval);
                    closeToast();
                    return MAX;
                }
                return newValue;
            });
        }, 45);

        return () => clearInterval(interval);
    }, [msg]); //eslint-disable-line

    if (!show) {
        return null;
    }

    return (
        <div
            id="toasty"
            className={`flex flex-col fixed z-[1000000] right-6 top-12 mt-10 ${error ? "border-t-2 border-t-red-500" : "border-t-2 border-t-green-500"} items-center w-full max-w-xs p-4 mb-4 rounded-lg shadow text-gray-400 bg-gray-800 `}
            role="alert"
        >
            <div className="flex items-center justify-between w-full space-x-2">
                <div className={alertType}>
                    <svg
                        className="w-5 h-5"
                        aria-hidden="true"
                        xmlns="http://www.w3.org/2000/svg"
                        fill="currentColor"
                        viewBox="0 0 20 20"
                    >
                        <path d="M10 .5a9.5 9.5 0 1 0 9.5 9.5A9.51 9.51 0 0 0 10 .5Zm3.707 8.207-4 4a1 1 0 0 1-1.414 0l-2-2a1 1 0 0 1 1.414-1.414L9 10.586l3.293-3.293a1 1 0 0 1 1.414 1.414Z" />
                    </svg>
                    <span className="sr-only">icon</span>
                </div>
                <div className="ml-3 text-sm font-normal whitespace-pre-line">
                    {msg}
                </div>
                <button
                    type="button"
                    className="ml-auto -mx-1.5 -my-1.5 rounded-lg focus:ring-2 focus:ring-gray-300 p-1.5 inline-flex items-center justify-center h-8 w-8 text-gray-500 hover:text-white bg-gray-800 hover:bg-gray-700"
                    aria-label="Close"
                    onClick={() => closeToast()}
                >
                    <span className="sr-only">Close</span>
                    <svg
                        className="w-3 h-3"
                        aria-hidden="true"
                        xmlns="http://www.w3.org/2000/svg"
                        fill="none"
                        viewBox="0 0 14 14"
                    >
                        <path
                            stroke="currentColor"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth="2"
                            d="m1 1 6 6m0 0 6 6M7 7l6-6M7 7l-6 6"
                        />
                    </svg>
                </button>
            </div>

            <progress
                className={`progress ${error ? "progress-error" : "progress-success"} top-4`}
                style={{ height: "3px", width: "100%" }}
                value={value}
                max={100}
            ></progress>
        </div>
    );
};

export default Message;

interface ScrollerProps {
    map?: L.Map | null;
    children: React.ReactNode;
    fromMain: boolean;
    scrollerName: string;
    showScroller: boolean;
    buttonCondition: boolean;
    hasFilteredData?: boolean;
    scrollerCondition: boolean;
    setShowScroller: React.Dispatch<React.SetStateAction<boolean>>;
}

const Scroller = ({
    map,
    children,
    fromMain,
    showScroller,
    scrollerName,
    buttonCondition,
    hasFilteredData,
    scrollerCondition,
    setShowScroller,
}: ScrollerProps) => {
    return (
        <>
            {buttonCondition ? (
                <button
                    className={
                        "btn absolute top-2 right-2 z-[10000] btn-sm  min-w-[180px] text-black" +
                        (hasFilteredData ? " btn-success" : " ")
                    }
                    style={{ backgroundColor: !hasFilteredData ? "white" : "" }}
                    onClick={() => setShowScroller(!showScroller)}
                >
                    {scrollerName}
                </button>
            ) : null}
            {scrollerCondition ? (
                <div
                    id="controller"
                    className="z-[1000000000] absolute top-12 right-2"
                    // Disable map dragging and zooming when hovering over the scroller
                    onMouseEnter={() => {
                        if (!fromMain) {
                            map?.dragging.disable();
                            map?.scrollWheelZoom.disable();
                        }
                    }}
                    onMouseLeave={() => {
                        if (!fromMain) {
                            map?.dragging.enable();
                            map?.scrollWheelZoom.enable();
                        }
                    }}
                >
                    <div
                        className={
                            fromMain
                                ? "overflow-y-auto max-h-[230px] h-auto w-[450px] ml-2 bg-white rounded-md scrollbar-thin scrollbar-thumb-rounded px-4 border border-gray-400"
                                : "overflow-y-auto max-h-[200px] h-auto max-w-[280px] min-w-[180px] ml-2 bg-white rounded-md scrollbar-thin px-4 border border-gray-400"
                        }
                    >
                        <div className="space-y-2 flex flex-col">
                            {children}
                        </div>
                    </div>
                </div>
            ) : null}
        </>
    );
};

export default Scroller;

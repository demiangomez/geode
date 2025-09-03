interface SquareSkeletonProps {
    mainSize?: string;
}

const SquareSkeleton = ({ mainSize }: SquareSkeletonProps) => {
    return (
        <div className="flex w-full justify-center">
            <div
                className={`skeleton ml-4 self-center w-11/12`}
                style={{
                    height: mainSize ?? "400px",
                    backgroundColor: "rgb(107 114 128 / 0.2)",
                }}
            ></div>
        </div>
    );
};

export default SquareSkeleton;
